import hashlib
import hmac
import json
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Account, CreditAccount, KYCApplication, VerifiedIdentity
from cards.models import BankCard, CardDelivery, CardOrder, CardProviderEvent, CardTransaction
from cards.services import approve_order, issue_order, place_order, reject_order

User = get_user_model()
_u = iter(range(1, 10**6))
SECRET = "s3cret"


def make_user(tier=0):
    n = next(_u)
    u = User.objects.create_user(f"u{n}", password="pass12345")
    return u, Account.objects.create(user=u, account_number=str(1000000000 + n), kyc_tier=tier)


def verify(user, name="Ada Chinwe Obi"):
    app = KYCApplication.objects.create(
        user=user, country_code="NG", document_type="nin", legal_name=name, date_of_birth=date(1990, 1, 1),
        identity_document="kyc/x.jpg", identity_digest=f"{user.pk:064d}", status="verified")
    VerifiedIdentity.objects.create(user=user, country_code="NG", document_type="nin",
                                    identity_digest=app.identity_digest, verified_at=timezone.now(), source_application=app)
    Account.objects.filter(user=user).update(kyc_tier=1)


def give_credit(acc, status="active"):
    return CreditAccount.objects.create(account=acc, credit_limit=Decimal("100000"), status=status)


PHYS = dict(address="12 Allen Avenue, Ikeja", city="Lagos", state="Lagos", phone="08012345678")


class OrderTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user, self.acc = make_user()

    def order(self, **kw):
        args = dict(card_type="debit", card_format="virtual", cardholder_name="ann lee")
        args.update(kw)
        return place_order(Account.objects.get(pk=self.acc.pk), **args)

    def test_a_virtual_debit_card_needs_no_id_and_the_name_is_tidied(self):
        o = self.order(cardholder_name="  ann   lee ")
        self.assertEqual((o.status, o.cardholder_name), ("pending", "ANN LEE"))

    def test_bad_names(self):
        for name in ["A", "ANN123", "", "X" * 27]:
            with self.assertRaises(ValidationError, msg=name):
                self.order(cardholder_name=name)
        self.assertEqual(CardOrder.objects.count(), 0)

    def test_physical_and_credit_cards_need_a_verified_id(self):
        with self.assertRaises(ValidationError):
            self.order(card_format="physical", **PHYS)
        give_credit(self.acc)
        with self.assertRaises(ValidationError):
            self.order(card_type="credit")

    def test_a_verified_customer_gets_the_legal_name_whatever_they_type(self):
        verify(self.user)
        o = self.order(cardholder_name="SOMEONE ELSE")
        self.assertEqual(o.cardholder_name, "ADA CHINWE OBI")

    def test_a_long_legal_name_drops_the_middle_names(self):
        verify(self.user, "Chukwuemeka Oluwaseun Adewale Ogunbanjo")
        self.assertEqual(self.order().cardholder_name, "CHUKWUEMEKA OGUNBANJO")

    def test_a_physical_card_needs_a_full_address_and_makes_a_delivery_record(self):
        verify(self.user)
        for bad in ({"address": "x"}, {"city": ""}, {"phone": "123"}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                self.order(card_format="physical", **{**PHYS, **bad})
        o = self.order(card_format="physical", **PHYS)
        self.assertTrue(CardDelivery.objects.filter(order=o).exists())
        self.assertEqual(o.delivery_phone, "+2348012345678")

    def test_a_credit_card_needs_an_active_facility(self):
        verify(self.user)
        with self.assertRaises(ValidationError):
            self.order(card_type="credit")
        give_credit(self.acc, "pending")
        with self.assertRaises(ValidationError):
            self.order(card_type="credit")

    def test_only_one_credit_card_or_credit_request(self):
        verify(self.user)
        give_credit(self.acc)
        self.order(card_type="credit")
        with self.assertRaises(ValidationError):
            self.order(card_type="credit")

    def test_duplicate_open_requests_and_the_card_limit(self):
        self.order()
        with self.assertRaises(ValidationError):
            self.order()
        for _ in range(3):
            BankCard.objects.create(account=self.acc, cardholder_name="A", last_four="1111", expiry_month=1, expiry_year=2035)
        CardOrder.objects.all().delete()
        with self.assertRaises(ValidationError):
            self.order()

    def test_a_closed_account_cannot_order(self):
        Account.objects.filter(pk=self.acc.pk).update(closed_at=timezone.now())
        with self.assertRaises(ValidationError):
            self.order()

    def test_the_dashboard_loads_with_a_pending_physical_order(self):
        verify(self.user)
        self.order(card_format="physical", **PHYS)
        c = Client()
        c.login(username=self.user.username, password="pass12345")
        self.assertEqual(c.get(reverse("accounts:home")).status_code, 200)


@override_settings(CARDS_ISSUER="cards.issuer.MockIssuer")
class StaffTests(TestCase):
    def setUp(self):
        self.user, self.acc = make_user()
        verify(self.user)
        give_credit(self.acc)
        self.o = place_order(self.acc, card_type="credit", card_format="virtual")

    def staff(self, perm=True):
        s = User.objects.create_user(f"s{next(_u)}", password="x", is_staff=True)
        if perm:
            s.user_permissions.add(Permission.objects.get(codename="approve_cardorder"))
        return User.objects.get(pk=s.pk)

    def test_only_staff_with_the_permission_can_approve(self):
        for who in (self.staff(perm=False), self.user):
            with self.assertRaises(PermissionDenied):
                approve_order(self.o.pk, who)
        self.o.refresh_from_db()
        self.assertEqual(self.o.status, "pending")

    def test_you_cannot_approve_your_own_request(self):
        me = self.user
        me.is_staff = True
        me.save()
        me.user_permissions.add(Permission.objects.get(codename="approve_cardorder"))
        with self.assertRaises(PermissionDenied):
            approve_order(self.o.pk, User.objects.get(pk=me.pk))

    def test_approve_then_issue_makes_a_linked_active_card(self):
        approve_order(self.o.pk, self.staff())
        order = issue_order(self.o.pk)
        self.assertEqual(order.status, "issued")
        self.assertEqual((order.bank_card.card_type, order.bank_card.status), ("credit", "active"))
        self.assertTrue(order.bank_card.provider_card_id)
        with self.assertRaises(ValidationError):
            issue_order(self.o.pk)                              # never issued twice

    def test_an_unapproved_order_cannot_be_issued(self):
        with self.assertRaises(ValidationError):
            issue_order(self.o.pk)

    def test_a_credit_request_is_refused_if_the_facility_was_closed(self):
        CreditAccount.objects.filter(account=self.acc).update(status="closed")
        with self.assertRaises(ValidationError):
            approve_order(self.o.pk, self.staff())

    def test_an_issuer_failure_can_be_retried(self):
        approve_order(self.o.pk, self.staff())
        with mock.patch("cards.issuer.MockIssuer.issue", side_effect=RuntimeError("down")):
            with self.assertRaises(ValidationError):
                issue_order(self.o.pk)
        self.o.refresh_from_db()
        self.assertEqual(self.o.status, "failed")
        self.assertEqual(issue_order(self.o.pk).status, "issued")
        self.assertEqual(BankCard.objects.count(), 1)

    def test_rejecting_needs_a_reason(self):
        with self.assertRaises(ValidationError):
            reject_order(self.o.pk, self.staff(), " ")
        reject_order(self.o.pk, self.staff(), "Not eligible")
        self.o.refresh_from_db()
        self.assertEqual((self.o.status, self.o.rejection_reason), ("rejected", "Not eligible"))


@override_settings(CARDS_WEBHOOK_SECRET=SECRET)
class WebhookTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user, self.acc = make_user()
        self.credit = give_credit(self.acc)
        self.card = BankCard.objects.create(
            account=self.acc, card_type="credit", cardholder_name="ANN", last_four="1234",
            expiry_month=12, expiry_year=timezone.localdate().year + 2, provider_card_id="CARD-1")
        self.url = reverse("cards:webhook")

    def send(self, payload, secret=SECRET, raw=None):
        body = raw or json.dumps(payload).encode()
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return Client().post(self.url, data=body, content_type="application/json", HTTP_X_SIGNATURE=sig)

    def auth(self, tx="TX1", amount="1500.00", event="E1", **kw):
        p = {"type": "authorization", "event_id": event, "provider_transaction_id": tx, "card_id": "CARD-1",
             "amount": amount, "currency": "NGN", "merchant": "Shop", "channel": "pos"}
        p.update(kw)
        return self.send(p)

    def owed(self):
        self.credit.refresh_from_db()
        return self.credit.outstanding_balance

    def test_an_authorisation_is_approved_and_recorded(self):
        r = self.auth()
        self.assertEqual(r.json()["approved"], True)
        self.assertEqual(self.owed(), Decimal("1500"))
        self.assertEqual(CardTransaction.objects.get().status, "authorized")

    def test_the_same_event_twice_spends_once(self):
        self.auth()
        r = self.auth()
        self.assertEqual(r.json()["approved"], True)
        self.assertEqual(self.owed(), Decimal("1500"))
        self.assertEqual(CardProviderEvent.objects.count(), 1)

    def test_declines_leave_the_credit_alone(self):
        cases = [self.auth(tx="A", event="A", amount="100000.01"), self.auth(tx="B", event="B", currency="USD"),
                 self.auth(tx="C", event="C", card_id="NOPE"), self.auth(tx="D", event="D", amount="abc")]
        for r in cases:
            self.assertFalse(r.json()["approved"])
        self.assertEqual(self.owed(), Decimal("0"))

    def test_online_payments_can_be_switched_off(self):
        BankCard.objects.filter(pk=self.card.pk).update(online_enabled=False)
        self.assertFalse(self.auth(channel="online").json()["approved"])
        self.assertTrue(self.auth(tx="TX2", event="E2", channel="pos").json()["approved"])

    def test_a_blocked_card_is_declined(self):
        BankCard.objects.filter(pk=self.card.pk).update(status="blocked")
        self.assertFalse(self.auth().json()["approved"])

    def test_bad_signatures_and_a_missing_secret_do_nothing(self):
        p = {"type": "authorization", "event_id": "X", "provider_transaction_id": "X", "card_id": "CARD-1", "amount": "10"}
        self.assertEqual(self.send(p, secret="wrong").status_code, 401)
        self.assertEqual(Client().post(self.url, data=json.dumps(p), content_type="application/json").status_code, 401)
        with override_settings(CARDS_WEBHOOK_SECRET=""):
            self.assertEqual(self.send(p, secret="").status_code, 401)
        self.assertEqual(self.owed(), Decimal("0"))

    def test_malformed_requests(self):
        self.assertEqual(self.send(None, raw=b"not json").status_code, 400)
        self.assertEqual(self.send({"nothing": 1}).status_code, 400)
        self.assertEqual(self.send({"type": "mystery", "event_id": "1"}).status_code, 400)

    def test_a_refund_restores_credit_once(self):
        self.auth()
        refund = {"type": "refund", "event_id": "R1", "original_transaction_id": "TX1", "amount": "1500.00"}
        self.send(refund)
        self.send({**refund, "event_id": "R2"})
        self.assertEqual(self.owed(), Decimal("0"))
        self.assertEqual(CardTransaction.objects.get().status, "reversed")

    def test_a_refund_for_an_unknown_purchase_is_ignored(self):
        r = self.send({"type": "reversal", "event_id": "Z", "original_transaction_id": "NOPE"})
        self.assertEqual(r.json()["applied"], False)