from decimal import Decimal

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from funding.allocation import approve_investor_allocation
from funding.capital import retained_profit_balance
from funding.contributor import confirm_received, create_intent, verify_contribution
from funding.models import CapitalContribution, ContributionIntent, FundingPeriod, InvestorAllocation, InvestorCapitalEntry
from funding.period import start_period_review
from .helpers import FundingBase, staff_with


def login(user):
    c = Client()
    c.login(username=user.username, password="test-password-123")
    return c


class ScreenTests(FundingBase):
    def setUp(self):
        super().setUp()
        self.finance = staff_with("fin", "confirm_contributionintent", "view_fundingperiod", "propose_investorallocation",
                                  "review_fundingperiod", "add_fundingperiod")
        self.checker = staff_with("chk", "verify_capitalcontribution", "view_fundingperiod", "approve_investorallocation",
                                  "add_investorcapitalentry", "close_fundingperiod")

    def contribution(self):
        intent = create_intent(self.investor_user, "50000", "bank_transfer")
        confirm_received(intent.pk, self.finance)
        return CapitalContribution.objects.get()

    def test_money_arrived_and_verify_buttons_work(self):
        intent = create_intent(self.investor_user, "50000", "bank_transfer")
        login(self.finance).post(reverse("funding:intents"), {"pk": intent.pk, "action": "confirm"})
        intent.refresh_from_db()
        self.assertEqual(intent.status, "received")
        login(self.checker).post(reverse("funding:contributions"), {"pk": intent.contribution_id})
        intent.contribution.refresh_from_db()
        self.assertTrue(intent.contribution.verified)
        self.assertEqual(InvestorCapitalEntry.objects.count(), 1)

    def test_a_non_investor_is_sent_away_politely(self):
        r = login(self.finance).get(reverse("funding:my_deposits"))
        self.assertEqual(r.status_code, 302)

    def test_periods_can_be_opened_reviewed_and_closed_from_the_screens(self):
        FundingPeriod.objects.all().delete()
        today = timezone.localdate()
        c = login(self.finance)
        c.post(reverse("funding:period_open"), {"start_date": today.replace(day=1), "end_date": today.replace(day=28)})
        period = FundingPeriod.objects.get()
        c.post(reverse("funding:period_open"), {"start_date": today.replace(day=10), "end_date": today.replace(day=28)})
        self.assertEqual(FundingPeriod.objects.count(), 1)                     # overlap refused
        c.post(reverse("funding:period", args=[period.pk]), {"action": "review"})
        period.refresh_from_db()
        self.assertEqual(period.status, "review")
        login(self.checker).post(reverse("funding:period", args=[period.pk]), {"action": "close"})
        period.refresh_from_db()
        self.assertEqual(period.status, "closed")

    def test_a_customer_cannot_open_the_period_screens(self):
        c = login(self.investor_user)
        self.assertEqual(c.get(reverse("funding:periods")).status_code, 403)
        self.assertEqual(c.post(reverse("funding:period_open"), {}).status_code, 302)    # message, nothing created

    def test_profit_is_shared_approved_and_posted_while_the_period_is_in_review(self):
        c = self.contribution()
        verify_contribution(c.pk, self.checker)
        start_period_review(period_id=self.period.pk, performed_by=self.finance)
        url = reverse("funding:period", args=[self.period.pk])
        login(self.finance).post(url, {"action": "propose", "result": "1000"})
        alloc = InvestorAllocation.objects.get()
        self.assertEqual(alloc.amount, Decimal("1000.00"))
        login(self.finance).post(url, {"action": "approve", "pk": alloc.pk})          # the proposer cannot approve
        alloc.refresh_from_db()
        self.assertEqual(alloc.approval_status, "pending")
        login(self.checker).post(url, {"action": "approve", "pk": alloc.pk})
        login(self.checker).post(url, {"action": "post", "pk": alloc.pk})
        self.assertEqual(retained_profit_balance(self.investor), Decimal("1000.00"))
        login(self.checker).post(url, {"action": "post", "pk": alloc.pk})              # a second post changes nothing
        self.assertEqual(InvestorCapitalEntry.objects.filter(entry_type="retained_profit").count(), 1)

    def test_a_period_cannot_be_shared_twice_or_closed_with_a_share_waiting(self):
        c = self.contribution()
        verify_contribution(c.pk, self.checker)
        start_period_review(period_id=self.period.pk, performed_by=self.finance)
        url = reverse("funding:period", args=[self.period.pk])
        login(self.finance).post(url, {"action": "propose", "result": "1000"})
        login(self.finance).post(url, {"action": "propose", "result": "2000"})
        self.assertEqual(InvestorAllocation.objects.count(), 1)
        login(self.checker).post(url, {"action": "close"})
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, "review")

    def test_bad_results_are_refused(self):
        self.set_period("review")
        url = reverse("funding:period", args=[self.period.pk])
        for bad in ("abc", "0", "", "NaN"):
            login(self.finance).post(url, {"action": "propose", "result": bad})
        self.assertFalse(InvestorAllocation.objects.exists())