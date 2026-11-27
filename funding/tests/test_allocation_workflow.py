from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError

from funding.accounting import post_investor_capital_event
from funding.allocation import (
    approve_investor_allocation,
    post_allocation_to_capital,
    reject_investor_allocation,
)
from funding.capital import capital_balance, retained_profit_balance
from funding.models import InvestorAgreement, InvestorAllocation, InvestorCapitalEntry
from funding.period import close_funding_period
from funding.services import allocate_monthly_result
from .helpers import FundingBase, staff_with


class AllocationWorkflowTests(FundingBase):
    """Propose -> approve -> post -> close, with the period in review the whole way."""

    def setUp(self):
        super().setUp()
        self.closer = staff_with("fund_closer", "close_fundingperiod")
        # Capital that is in place for the whole period, so the capital component has something to share.
        post_investor_capital_event(
            posted_by=self.admin_user, investor_id=self.investor.pk, entry_type="contribution",
            amount=Decimal("10000.00"), effective_date=self.period.start_date, reference="WF-CONTRIB-1")

    def propose(self, result="1000.00", who=None):
        self.set_period("review")
        return allocate_monthly_result(self.period.pk, result, proposed_by=who or self.proposer)

    # ----------------------------------------------------------------- proposing
    def test_proposing_creates_pending_allocations_with_the_proposer(self):
        (row,) = self.propose("1000.00")
        self.assertEqual((row.allocation_type, row.amount, row.approval_status), ("profit", Decimal("1000.00"), "pending"))
        self.assertEqual((row.capital_component, row.agreed_component), (Decimal("500.00"), Decimal("500.00")))
        self.assertEqual(row.proposed_by, self.proposer)

    def test_a_negative_result_is_a_loss(self):
        (row,) = self.propose("-400.00")
        self.assertEqual((row.allocation_type, row.amount), ("loss", Decimal("400.00")))

    def test_proposing_needs_the_permission_and_a_review_period(self):
        self.set_period("review")
        with self.assertRaises(PermissionDenied):
            allocate_monthly_result(self.period.pk, "1000", proposed_by=staff_with("plain_staff"))
        self.set_period("open")
        with self.assertRaises(ValidationError):
            allocate_monthly_result(self.period.pk, "1000", proposed_by=self.proposer)

    def test_proposing_again_replaces_pending_proposals(self):
        self.propose("1000.00")
        self.propose("2000.00")
        rows = InvestorAllocation.objects.filter(period=self.period)
        self.assertEqual((rows.count(), rows.get().amount), (1, Decimal("2000.00")))

    def test_approved_allocations_are_never_replaced(self):
        (row,) = self.propose("1000.00")
        approve_investor_allocation(allocation_id=row.pk, approved_by=self.admin_user)
        with self.assertRaises(ValidationError):
            allocate_monthly_result(self.period.pk, "2000", proposed_by=self.proposer)
        self.assertEqual(InvestorAllocation.objects.get(period=self.period).amount, Decimal("1000.00"))

    # ----------------------------------------------------------------- rejecting
    def test_a_different_person_can_reject_and_the_period_can_be_proposed_again(self):
        (row,) = self.propose("1000.00")
        with self.assertRaises(PermissionDenied):
            reject_investor_allocation(allocation_id=row.pk, rejected_by=self.proposer)
        done = reject_investor_allocation(allocation_id=row.pk, rejected_by=self.admin_user)
        self.assertEqual(done.approval_status, "rejected")
        self.assertEqual(len(allocate_monthly_result(self.period.pk, "1500", proposed_by=self.proposer)), 1)

    # ------------------------------------------------------------------- posting
    def test_an_approved_profit_is_posted_as_retained_profit_while_in_review(self):
        (row,) = self.propose("1000.00")
        approve_investor_allocation(allocation_id=row.pk, approved_by=self.admin_user)
        (entry,) = post_allocation_to_capital(allocation_id=row.pk, posted_by=self.admin_user)
        self.assertEqual((entry.entry_type, entry.amount, entry.journal_entry.status), ("retained_profit", Decimal("1000.00"), "posted"))
        self.assertEqual(retained_profit_balance(self.investor), Decimal("1000.00"))
        self.assertEqual(capital_balance(self.investor), Decimal("10000.00"))

    def test_the_reinvest_treatment_moves_the_profit_into_capital(self):
        InvestorAgreement.objects.filter(pk=self.agreement.pk).update(profit_treatment="reinvest")
        (row,) = self.propose("1000.00")
        approve_investor_allocation(allocation_id=row.pk, approved_by=self.admin_user)
        entries = post_allocation_to_capital(allocation_id=row.pk, posted_by=self.admin_user)
        self.assertEqual([e.entry_type for e in entries], ["retained_profit", "reinvestment"])
        self.assertEqual(retained_profit_balance(self.investor), Decimal("0.00"))
        self.assertEqual(capital_balance(self.investor), Decimal("11000.00"))

    def test_the_payout_treatment_pays_the_profit_out(self):
        InvestorAgreement.objects.filter(pk=self.agreement.pk).update(profit_treatment="payout")
        (row,) = self.propose("1000.00")
        approve_investor_allocation(allocation_id=row.pk, approved_by=self.admin_user)
        entries = post_allocation_to_capital(allocation_id=row.pk, posted_by=self.admin_user)
        self.assertEqual([e.entry_type for e in entries], ["retained_profit", "payout"])
        self.assertEqual(retained_profit_balance(self.investor), Decimal("0.00"))

    def test_an_approved_loss_reduces_capital(self):
        (row,) = self.propose("-1000.00")
        approve_investor_allocation(allocation_id=row.pk, approved_by=self.admin_user)
        (entry,) = post_allocation_to_capital(allocation_id=row.pk, posted_by=self.admin_user)
        self.assertEqual(entry.entry_type, "loss")
        self.assertEqual(capital_balance(self.investor), Decimal("9000.00"))

    def test_an_allocation_is_posted_only_once_and_only_when_approved(self):
        (row,) = self.propose("1000.00")
        with self.assertRaises(ValidationError):
            post_allocation_to_capital(allocation_id=row.pk, posted_by=self.admin_user)
        approve_investor_allocation(allocation_id=row.pk, approved_by=self.admin_user)
        post_allocation_to_capital(allocation_id=row.pk, posted_by=self.admin_user)
        with self.assertRaises(ValidationError):
            post_allocation_to_capital(allocation_id=row.pk, posted_by=self.admin_user)
        self.assertEqual(InvestorCapitalEntry.objects.filter(allocation=row).count(), 1)

    def test_posting_needs_the_permission(self):
        (row,) = self.propose("1000.00")
        approve_investor_allocation(allocation_id=row.pk, approved_by=self.admin_user)
        with self.assertRaises(PermissionDenied):
            post_allocation_to_capital(allocation_id=row.pk, posted_by=staff_with("plain_staff"))

    # ------------------------------------------------------------------- closing
    def test_a_period_cannot_close_with_an_approved_allocation_left_unposted(self):
        (row,) = self.propose("1000.00")
        approve_investor_allocation(allocation_id=row.pk, approved_by=self.admin_user)
        with self.assertRaises(ValidationError):
            close_funding_period(period_id=self.period.pk, performed_by=self.closer)
        post_allocation_to_capital(allocation_id=row.pk, posted_by=self.admin_user)
        self.assertEqual(close_funding_period(period_id=self.period.pk, performed_by=self.closer).status, "closed")
