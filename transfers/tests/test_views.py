from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Account
from beneficiary.models import Beneficiary
from transactions.models import Transaction
from transfers.models import MoneyRequest, Transfer

User = get_user_model()


def make(name, number, balance="0"):
    u = User.objects.create_user(name, password="pass12345", first_name=name.title())
    a = Account.objects.create(user=u, account_number=number, balance=Decimal(balance))
    a.set_pin("4827")
    return u, a


@override_settings(KLOSA_BANK_CODE="999")
class SendViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.ua, self.a = make("ann", "1000000001", "10000")
        self.ub, self.b = make("ben", "1000000002")
        self.ben = Beneficiary.objects.create(owner=self.ua, name="Ben", account_number="1000000002",
                                              bank_code="999", bank_name="Klosa Bank")
        self.url = reverse("transfers:send")
        self.client.login(username="ann", password="pass12345")

    def post(self, amount="1000", pin="4827", token=None, ben=None):
        token = token or self.client.get(self.url).context["token"]
        return self.client.post(self.url, {"beneficiary": (ben or self.ben).pk, "amount": amount,
                                           "pin": pin, "token": token, "description": ""})

    def bal(self, a):
        a.refresh_from_db()
        return a.balance

    def test_success_redirects_to_a_receipt_that_exists(self):
        r = self.post()
        self.assertEqual(r.status_code, 302)
        tx = Transaction.objects.get(account=self.a, transaction_type="transfer")
        self.assertTrue(tx.reference)
        self.assertEqual(self.client.get(r["Location"]).status_code, 200)
        self.assertEqual(self.bal(self.b), Decimal("1000"))

    def test_wrong_pin_moves_nothing_and_is_counted(self):
        self.post(pin="0000")
        self.assertEqual(self.bal(self.a), Decimal("10000"))
        self.a.refresh_from_db()
        self.assertEqual(self.a.pin_attempts, 1)

    def test_same_token_cannot_be_reused(self):
        token = self.client.get(self.url).context["token"]
        self.post(token=token)
        self.post(token=token)
        self.assertEqual(self.bal(self.a), Decimal("9000"))

    def test_tier_zero_limit_is_enforced(self):
        Account.objects.filter(pk=self.a.pk).update(balance=Decimal("500000"))
        self.post(amount="25000")
        self.assertEqual(self.bal(self.a), Decimal("500000"))

    def test_closed_receiver_is_refused(self):
        Account.objects.filter(pk=self.b.pk).update(closed_at=timezone.now())
        self.post()
        self.assertEqual(self.bal(self.a), Decimal("10000"))

    def test_closed_sender_cannot_use_the_page(self):
        Account.objects.filter(pk=self.a.pk).update(closed_at=timezone.now())
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_other_bank_beneficiary_is_refused(self):
        other = Beneficiary.objects.create(owner=self.ua, name="X", account_number="1000000002",
                                           bank_code="058", bank_name="GTBank")
        self.post(ben=other)
        self.assertEqual(self.bal(self.a), Decimal("10000"))


@override_settings(KLOSA_BANK_CODE="999")
class RequestViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.ua, self.a = make("ann", "1000000001", "5000")
        self.ub, self.b = make("ben", "1000000002")
        self.client.login(username="ann", password="pass12345")

    def test_request_is_created_with_an_expiry(self):
        self.client.post(reverse("transfers:request_money"),
                         {"account_number": "1000000002", "amount": "500", "note": "hi"})
        r = MoneyRequest.objects.get()
        self.assertEqual(r.requester, self.ua)
        self.assertEqual(r.recipient, self.ub)
        self.assertGreater(r.expires_at, timezone.now() + timedelta(days=6))

    def test_unknown_closed_and_own_accounts_look_the_same_and_create_nothing(self):
        Account.objects.filter(pk=self.b.pk).update(closed_at=timezone.now())
        for num in ("9999999999", "1000000002", "1000000001"):
            r = self.client.post(reverse("transfers:request_money"),
                                 {"account_number": num, "amount": "5", "note": ""})
            self.assertEqual(r.status_code, 302)
        self.assertFalse(MoneyRequest.objects.exists())

    def test_pending_limit(self):
        for _ in range(12):
            self.client.post(reverse("transfers:request_money"),
                             {"account_number": "1000000002", "amount": "5", "note": ""})
        self.assertEqual(MoneyRequest.objects.count(), 10)

    def test_paying_needs_the_right_pin(self):
        req = MoneyRequest.objects.create(requester=self.ub, recipient=self.ua, amount=Decimal("100"),
                                          expires_at=timezone.now() + timedelta(days=1))
        url = reverse("transfers:pay_request", args=[req.pk])
        self.client.post(url, {"pin": "0000"})
        req.refresh_from_db()
        self.assertEqual(req.status, "pending")
        self.client.post(url, {"pin": "4827"})
        req.refresh_from_db()
        self.assertEqual(req.status, "paid")
        self.assertEqual(Transfer.objects.count(), 1)

    def test_only_the_recipient_can_pay_decline_or_cancel(self):
        req = MoneyRequest.objects.create(requester=self.ub, recipient=self.ua, amount=Decimal("100"))
        c = self.client_class()
        c.login(username="ben", password="pass12345")
        self.assertEqual(c.post(reverse("transfers:pay_request", args=[req.pk]), {"pin": "4827"}).status_code, 404)
        self.assertEqual(c.post(reverse("transfers:decline_request", args=[req.pk])).status_code, 404)
        req.refresh_from_db()
        self.assertEqual(req.status, "pending")