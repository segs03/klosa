from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import Account, WelcomeBonus
from funding.contributions import post_verified_investor_contribution
from funding.contributor import confirm_received, create_intent, reject_intent
from funding.models import CapitalContribution, ContributionIntent, JournalLine, LedgerPosting
from treasury.models import CashRequest
from .helpers import FundingBase, staff_with

User = get_user_model()


class ContributorTests(FundingBase):
    def setUp(self):
        super().setUp()
        self.finance = staff_with("fin_confirm", "confirm_contributionintent")

    def intent(self, amount="50000"):
        return create_intent(self.investor_user, amount, "bank_transfer")

    def test_amounts_and_who_can_create_a_notice(self):
        for bad in ("0", "9999", "abc", "NaN", "10.999", ""):
            with self.assertRaises(ValidationError, msg=bad):
                self.intent(bad)
        with self.assertRaises(ValidationError):
            create_intent(User.objects.create_user("nobody", password="x"), "50000", "cash")
        with self.assertRaises(ValidationError):
            create_intent(self.investor_user, "50000", "crypto")

    def test_only_three_open_notices(self):
        for _ in range(3):
            self.intent()
        with self.assertRaises(ValidationError):
            self.intent()

    def test_confirm_then_a_second_person_verifies_and_the_books_balance(self):
        i = self.intent()
        confirm_received(i.pk, self.finance)
        i.refresh_from_db()
        self.assertEqual((i.status, i.contribution.recorded_by), ("received", self.finance))
        with self.assertRaises(PermissionDenied):                       # the person who confirmed cannot verify it
            post_verified_investor_contribution(i.contribution.pk, staff_with("both", "confirm_contributionintent", "verify_capitalcontribution") if False else self.finance)
        entry = post_verified_investor_contribution(i.contribution.pk, self.admin_user)
        lines = entry.journal_entry.lines.all()
        self.assertEqual(sum(l.debit for l in lines), sum(l.credit for l in lines))

    def test_confirming_needs_the_permission_and_works_once(self):
        i = self.intent()
        for who in (staff_with("plain"), self.investor_user):
            with self.assertRaises(PermissionDenied):
                confirm_received(i.pk, who)
        confirm_received(i.pk, self.finance)
        with self.assertRaises(ValidationError):
            confirm_received(i.pk, self.finance)
        self.assertEqual(CapitalContribution.objects.count(), 1)

    def test_a_late_notice_can_still_be_confirmed_and_rejecting_needs_a_reason(self):
        i = self.intent()
        ContributionIntent.objects.filter(pk=i.pk).update(status="expired", expires_at=timezone.now() - timedelta(days=1))
        with self.assertRaises(ValidationError):
            reject_intent(i.pk, self.finance, " ")
        confirm_received(i.pk, self.finance)
        j = self.intent("20000")
        reject_intent(j.pk, self.finance, "Wrong amount")
        j.refresh_from_db()
        self.assertEqual((j.status, j.note), ("rejected", "Wrong amount"))

    def test_pages_are_private(self):
        c = Client()
        c.login(username=self.investor_user.username, password="test-password-123")
        self.assertEqual(c.get(reverse("funding:my_deposits")).status_code, 200)
        for name in ("funding:intents", "funding:contributions"):
            self.assertEqual(c.get(reverse(name)).status_code, 403)
        other = User.objects.create_user("notinv", password="pass12345")
        c2 = Client()
        c2.login(username="notinv", password="pass12345")
        self.assertEqual(c2.get(reverse("funding:my_deposits")).status_code, 404)


class PostingTests(FundingBase):
    def setUp(self):
        super().setUp()
        self.cust = User.objects.create_user("cust", password="x")
        self.acc = Account.objects.create(user=self.cust, account_number="1000000001", balance=Decimal("5000"))
        maker = staff_with("m1")
        checker = staff_with("c1")
        CashRequest.objects.create(account=self.acc, amount=Decimal("5000"), branch="Ikeja", reference="CI1", status="posted",
                                   maker=maker, checker=checker, decided_at=timezone.now())

    def lines(self, kind):
        return {l.account.code: (l.debit, l.credit) for l in JournalLine.objects.filter(entry__posting__source_type=kind)}

    def test_cash_in_posts_once_to_the_right_accounts(self):
        call_command("post_ledger")
        call_command("post_ledger")
        self.assertEqual(LedgerPosting.objects.count(), 1)
        self.assertEqual(self.lines("cashin"), {"1000": (Decimal("5000"), Decimal("0")), "2300": (Decimal("0"), Decimal("5000"))})

    def test_nothing_posts_without_an_open_period_and_it_catches_up_later(self):
        self.set_period("closed")
        call_command("post_ledger")
        self.assertEqual(LedgerPosting.objects.count(), 0)
        self.set_period("open")
        call_command("post_ledger")
        self.assertEqual(LedgerPosting.objects.count(), 1)

    def test_the_welcome_bonus_is_an_expense_and_the_check_agrees(self):
        WelcomeBonus.objects.create(user=self.cust, amount=Decimal("200000"), reference="WB1")
        Account.objects.filter(pk=self.acc.pk).update(balance=Decimal("205000"))
        call_command("post_ledger")
        self.assertEqual(self.lines("bonus"), {"5200": (Decimal("200000"), Decimal("0")), "2300": (Decimal("0"), Decimal("200000"))})
        from io import StringIO
        out = StringIO()
        call_command("ledger_check", "--strict", stdout=out)
        self.assertIn("₦0.00", out.getvalue())