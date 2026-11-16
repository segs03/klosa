from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.credit_services import submit_credit_application
from accounts.models import Account, CreditAccount, CreditApplication, CreditPolicy
from backoffice.models import AuditLog
from biller.models import BillPayment
from cards.models import CardOrder
from cards.services import place_order
from treasury.models import CashRequest
from treasury.services import create_cash_in

User = get_user_model()
_n = iter(range(1, 10**6))


def staff(*codes):
    s = User.objects.create_user(f"s{next(_n)}", password="pass12345", is_staff=True)
    if codes:
        s.user_permissions.add(*Permission.objects.filter(codename__in=codes))
    return User.objects.get(pk=s.pk)


def client_for(user):
    c = Client()
    c.login(username=user.username, password="pass12345")
    return c


def customer(tier=0, balance="0"):
    n = next(_n)
    u = User.objects.create_user(f"c{n}", password="pass12345", first_name="Ann", last_name="Lee")
    return u, Account.objects.create(user=u, account_number=str(1000000000 + n), kyc_tier=tier, balance=Decimal(balance))


class AccessTests(TestCase):
    TOOLS = ["backoffice:cards", "backoffice:credit", "backoffice:cash", "backoffice:customers",
             "backoffice:ops", "backoffice:audit"]

    def test_anonymous_users_go_to_login(self):
        for name in ["backoffice:home"] + self.TOOLS:
            self.assertEqual(Client().get(reverse(name)).status_code, 302, name)

    def test_customers_are_forbidden(self):
        u, _ = customer()
        for name in ["backoffice:home"] + self.TOOLS:
            self.assertEqual(client_for(u).get(reverse(name)).status_code, 403, name)

    def test_staff_without_permissions_see_an_empty_console(self):
        c = client_for(staff())
        self.assertEqual(c.get(reverse("backoffice:home")).status_code, 200)
        for name in self.TOOLS:
            self.assertEqual(c.get(reverse(name)).status_code, 403, name)

    def test_the_right_permission_opens_the_right_tool(self):
        pairs = {"backoffice:cards": "approve_cardorder", "backoffice:credit": "assess_creditapplication",
                 "backoffice:cash": "approve_cash", "backoffice:customers": "view_customers",
                 "backoffice:ops": "view_operations", "backoffice:audit": "view_audit"}
        for name, code in pairs.items():
            self.assertEqual(client_for(staff(code)).get(reverse(name)).status_code, 200, name)

    def test_the_home_page_lists_only_your_tools(self):
        r = client_for(staff("view_customers")).get(reverse("backoffice:home"))
        self.assertContains(r, "Customers")
        self.assertNotContains(r, "Card requests")


@override_settings(CARDS_ISSUER="cards.issuer.MockIssuer")
class CardConsoleTests(TestCase):
    def setUp(self):
        self.u, self.acc = customer()
        self.order = place_order(self.acc, card_type="debit", card_format="virtual", cardholder_name="ANN LEE")
        self.s = staff("approve_cardorder")
        self.c = client_for(self.s)
        self.url = reverse("backoffice:card_detail", args=[self.order.pk])

    def test_the_request_is_listed(self):
        self.assertContains(self.c.get(reverse("backoffice:cards")), "ANN LEE")

    def test_approve_then_issue_is_logged(self):
        self.c.post(self.url, {"action": "approve"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "approved")
        self.c.post(self.url, {"action": "issue"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "issued")
        self.assertTrue(self.order.bank_card)
        self.assertEqual(set(AuditLog.objects.values_list("action", flat=True)), {"card_approve", "card_issue"})

    def test_rejecting_needs_a_reason(self):
        self.c.post(self.url, {"action": "reject", "reason": ""})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "pending")
        self.c.post(self.url, {"action": "reject", "reason": "Not eligible"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "rejected")

    def test_you_cannot_approve_your_own_request(self):
        mine = Account.objects.create(user=self.s, account_number="1999999999")
        own = place_order(mine, card_type="debit", card_format="virtual", cardholder_name="STAFF ONE")
        self.c.post(reverse("backoffice:card_detail", args=[own.pk]), {"action": "approve"})
        own.refresh_from_db()
        self.assertEqual(own.status, "pending")

    def test_a_customer_cannot_use_the_page(self):
        self.assertEqual(client_for(self.u).post(self.url, {"action": "approve"}).status_code, 403)


@override_settings(CREDIT_MIN_ACCOUNT_AGE_DAYS=0, CREDIT_MIN_BALANCE="0")
class CreditConsoleTests(TestCase):
    def setUp(self):
        CreditPolicy.objects.create(account_level="basic", currency="NGN",
                                    minimum_limit=Decimal("1000"), maximum_limit=Decimal("500000"))
        self.u, self.acc = customer(balance="20000")
        self.app = submit_credit_application(user=self.u, requested_amount="100000")
        self.officer = staff("assess_creditapplication")
        self.approver = staff("approve_creditapplication")
        self.url = reverse("backoffice:credit_detail", args=[self.app.pk])

    def test_assess_then_approve_opens_the_facility(self):
        client_for(self.officer).post(self.url, {"action": "assess", "amount": "80000", "reason": "Good history"})
        self.app.refresh_from_db()
        self.assertEqual((self.app.status, self.app.assessed_amount), ("assessed", Decimal("80000")))
        client_for(self.approver).post(self.url, {"action": "approved", "reason": "Agreed"})
        self.app.refresh_from_db()
        credit = CreditAccount.objects.get(account=self.acc)
        self.assertEqual((self.app.status, credit.status, credit.credit_limit), ("approved", "active", Decimal("80000")))

    def test_the_assessor_cannot_also_decide(self):
        both = staff("assess_creditapplication", "approve_creditapplication")
        c = client_for(both)
        c.post(self.url, {"action": "assess", "amount": "80000", "reason": "ok"})
        c.post(self.url, {"action": "approved", "reason": "ok"})
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, "assessed")
        self.assertFalse(CreditAccount.objects.filter(account=self.acc).exists())

    def test_each_permission_does_only_its_own_job(self):
        client_for(self.approver).post(self.url, {"action": "assess", "amount": "80000", "reason": "ok"})
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, "pending")

    def test_one_request_in_progress_at_a_time(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            submit_credit_application(user=self.u, requested_amount="5000")

    @override_settings(CREDIT_MIN_ACCOUNT_AGE_DAYS=30)
    def test_the_age_rule_is_explained_with_the_real_numbers(self):
        r = client_for(self.officer).post(self.url, {"action": "assess", "amount": "80000", "reason": "ok"}, follow=True)
        self.assertContains(r, "at least 30 days old")
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, "pending")


class CashConsoleTests(TestCase):
    def setUp(self):
        self.u, self.acc = customer()
        self.teller = staff("record_cash")
        self.approver = staff("approve_cash")
        self.req = create_cash_in(self.teller, self.acc.account_number, "5000", "Ikeja")

    def bal(self):
        self.acc.refresh_from_db()
        return self.acc.balance

    def test_approving_credits_once(self):
        c = client_for(self.approver)
        url = reverse("backoffice:cash_decide", args=[self.req.pk])
        c.post(url, {"action": "approve"})
        c.post(url, {"action": "approve"})
        self.assertEqual(self.bal(), Decimal("5000"))
        self.assertTrue(AuditLog.objects.filter(action="cash_approve").exists())

    def test_the_person_who_recorded_it_cannot_approve_it(self):
        both = staff("record_cash", "approve_cash")
        req = create_cash_in(both, self.acc.account_number, "1000", "Ikeja")
        client_for(both).post(reverse("backoffice:cash_decide", args=[req.pk]), {"action": "approve"})
        req.refresh_from_db()
        self.assertEqual(req.status, "pending")

    def test_deciding_needs_post_and_permission(self):
        url = reverse("backoffice:cash_decide", args=[self.req.pk])
        self.assertEqual(client_for(self.approver).get(url).status_code, 405)
        self.assertEqual(client_for(self.teller).post(url, {"action": "approve"}).status_code, 403)


class CustomerConsoleTests(TestCase):
    def setUp(self):
        self.u, self.acc = customer(balance="1000")
        User.objects.filter(pk=self.u.pk).update(phone_number="+2348012345678")
        self.mgr = staff("view_customers", "manage_customers")
        self.viewer = staff("view_customers")
        self.url = reverse("backoffice:customer", args=[self.u.pk])

    def test_search_by_account_number_phone_and_name(self):
        c = client_for(self.viewer)
        for q in (self.acc.account_number, "08012345678", "Ann"):
            self.assertContains(c.get(reverse("backoffice:customers"), {"q": q}), "Ann Lee")
        self.assertNotContains(c.get(reverse("backoffice:customers"), {"q": "nobody"}), "Ann Lee")

    def test_unlocking_clears_the_lock_and_is_logged(self):
        Account.objects.filter(pk=self.acc.pk).update(pin_attempts=2, locked_until=timezone.now() + timedelta(minutes=10))
        client_for(self.mgr).post(self.url, {"action": "unlock", "reason": "Called and verified"})
        self.acc.refresh_from_db()
        self.assertEqual((self.acc.pin_attempts, self.acc.locked_until), (0, None))
        self.assertEqual(AuditLog.objects.get().detail, "Called and verified")

    def test_a_reason_is_required(self):
        client_for(self.mgr).post(self.url, {"action": "suspend", "reason": ""})
        self.u.refresh_from_db()
        self.assertTrue(self.u.is_active)

    def test_suspending_stops_login_and_reactivating_restores_it(self):
        c = client_for(self.mgr)
        c.post(self.url, {"action": "suspend", "reason": "Fraud report"})
        self.u.refresh_from_db()
        self.assertFalse(self.u.is_active)
        self.assertFalse(Client().login(username=self.u.username, password="pass12345"))
        c.post(self.url, {"action": "reactivate", "reason": "Cleared"})
        self.u.refresh_from_db()
        self.assertTrue(self.u.is_active)

    def test_a_closed_account_cannot_be_reactivated(self):
        Account.objects.filter(pk=self.acc.pk).update(closed_at=timezone.now())
        User.objects.filter(pk=self.u.pk).update(is_active=False)
        client_for(self.mgr).post(self.url, {"action": "reactivate", "reason": "Please"})
        self.u.refresh_from_db()
        self.assertFalse(self.u.is_active)

    def test_a_viewer_can_look_but_not_change(self):
        c = client_for(self.viewer)
        self.assertEqual(c.get(self.url).status_code, 200)
        self.assertEqual(c.post(self.url, {"action": "suspend", "reason": "x"}).status_code, 403)

    def test_staff_accounts_are_not_shown_as_customers(self):
        other = staff()
        self.assertEqual(client_for(self.viewer).get(reverse("backoffice:customer", args=[other.pk])).status_code, 404)


class OperationsAndAuditTests(TestCase):
    def test_old_processing_bills_are_listed(self):
        u, acc = customer()
        b = BillPayment.objects.create(account=acc, category="airtime", provider_code="mtn",
                                       customer_ref="08010001234", amount=Decimal("500"), reference="BPSTUCK0001")
        BillPayment.objects.filter(pk=b.pk).update(created_at=timezone.now() - timedelta(hours=2))
        self.assertContains(client_for(staff("view_operations")).get(reverse("backoffice:ops")), "BPSTUCK0001")

    def test_recent_ones_are_not(self):
        u, acc = customer()
        BillPayment.objects.create(account=acc, category="airtime", provider_code="mtn",
                                   customer_ref="08010001234", amount=Decimal("500"), reference="BPFRESH0001")
        self.assertNotContains(client_for(staff("view_operations")).get(reverse("backoffice:ops")), "BPFRESH0001")

    def test_the_audit_log_is_readable_and_cannot_be_edited_in_the_admin(self):
        s = staff("view_audit")
        AuditLog.objects.create(staff=s, action="unlock_pin", target="user:1", detail="Verified")
        self.assertContains(client_for(s).get(reverse("backoffice:audit")), "unlock_pin")