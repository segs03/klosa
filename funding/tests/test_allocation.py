from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError

from funding.accounting import post_investor_capital_event
from funding.allocation import approve_investor_allocation
from funding.models import InvestorAllocation
from .helpers import FundingBase, staff_with


class InvestorAllocationApprovalTests(FundingBase):

    def test_a_different_person_approves_in_review(self):
        allocation = self.create_allocation()
        done = self.approve(allocation)
        self.assertEqual((done.approval_status, done.approved_by), ("approved", self.admin_user))

    def test_staff_without_approval_permission_cannot_approve(self):
        allocation = self.create_allocation()
        self.set_period("review")
        with self.assertRaises(PermissionDenied):
            approve_investor_allocation(allocation_id=allocation.pk, approved_by=staff_with("plain_staff"))

    def test_investor_and_inactive_staff_cannot_approve(self):
        allocation = self.create_allocation()
        self.set_period("review")
        inactive = staff_with("inactive_admin", "approve_investorallocation", active=False)
        for who in (self.investor_user, inactive):
            with self.assertRaises(PermissionDenied):
                approve_investor_allocation(allocation_id=allocation.pk, approved_by=who)

    def test_the_proposer_cannot_approve_their_own_allocation(self):
        allocation = self.create_allocation()
        both = staff_with("both_roles", "propose_investorallocation", "approve_investorallocation")
        InvestorAllocation.objects.filter(pk=allocation.pk).update(proposed_by=both)
        self.set_period("review")
        with self.assertRaises(PermissionDenied):
            approve_investor_allocation(allocation_id=allocation.pk, approved_by=both)

    def test_an_allocation_with_no_proposer_on_record_is_refused(self):
        allocation = self.create_allocation()
        InvestorAllocation.objects.filter(pk=allocation.pk).update(proposed_by=None)
        self.set_period("review")
        with self.assertRaises(ValidationError):
            approve_investor_allocation(allocation_id=allocation.pk, approved_by=self.admin_user)

    def test_it_can_only_be_approved_while_the_period_is_in_review(self):
        allocation = self.create_allocation()
        for status in ("open", "closed"):
            self.set_period(status)
            with self.assertRaises(ValidationError):
                approve_investor_allocation(allocation_id=allocation.pk, approved_by=self.admin_user)

    def test_approved_allocation_cannot_be_edited(self):
        allocation = self.approve(self.create_allocation())
        allocation.refresh_from_db()
        allocation.amount += Decimal("100.00")
        with self.assertRaises(ValidationError):
            allocation.save()

    def test_approved_allocation_cannot_be_deleted(self):
        allocation = self.approve(self.create_allocation())
        with self.assertRaises(ValidationError):
            allocation.delete()
        self.assertTrue(InvestorAllocation.objects.filter(pk=allocation.pk).exists())

    def test_allocation_cannot_be_approved_twice(self):
        allocation = self.create_allocation()
        self.approve(allocation)
        with self.assertRaises(ValidationError):
            self.approve(allocation)

    def test_unapproved_allocation_cannot_be_posted(self):
        allocation = self.create_allocation()
        with self.assertRaises(ValidationError):
            post_investor_capital_event(
                posted_by=self.admin_user, investor_id=self.investor.pk, entry_type="retained_profit",
                amount=Decimal("2000.00"), effective_date=self.today, reference="UNAPPROVED-PROFIT-001",
                allocation=allocation)

