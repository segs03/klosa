import calendar
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from funding.allocation import approve_investor_allocation
from funding.models import FundingPeriod, Investor, InvestorAgreement, InvestorAllocation

User = get_user_model()


def staff_with(username, *codes, active=True):
    user = User.objects.create_user(username=username, password="test-password-123", is_staff=True, is_active=active)
    if codes:
        user.user_permissions.add(*Permission.objects.filter(codename__in=codes))
    return User.objects.get(pk=user.pk)


class FundingBase(TestCase):
    """Two different staff members, as the real rules require: one proposes and records, another approves and posts."""

    def setUp(self):
        call_command("seed_ledger")
        self.today = timezone.localdate()
        self.proposer = staff_with("fund_proposer", "propose_investorallocation", "add_capitalcontribution",
                                   "add_investorcapitalentry")
        self.admin_user = staff_with("fund_approver", "approve_investorallocation", "verify_capitalcontribution",
                                     "add_investorcapitalentry")
        self.investor_user = User.objects.create_user(username="investor_user", password="test-password-123")
        self.investor = Investor.objects.create(user=self.investor_user, legal_name="Test Investor")

        first = self.today.replace(day=1)
        last = self.today.replace(day=calendar.monthrange(self.today.year, self.today.month)[1])
        self.period = FundingPeriod.objects.create(start_date=first, end_date=last, status="open")
        self.agreement = self.make_agreement(self.investor, first)

    @staticmethod
    def make_agreement(investor, start):
        return InvestorAgreement.objects.create(
            investor=investor, capital_basis="time_weighted_average", profit_treatment="retain",
            effective_from=start, profit_capital_component=Decimal("50.00"), profit_agreed_component=Decimal("50.00"),
            agreed_profit_share=Decimal("100.00"), loss_capital_component=Decimal("50.00"),
            loss_agreed_component=Decimal("50.00"), agreed_loss_share=Decimal("100.00"),
            signed_document_reference="test-doc", approved=True)

    def set_period(self, status):
        FundingPeriod.objects.filter(pk=self.period.pk).update(status=status)

    def create_allocation(self, allocation_type="profit", amount="2000.00", investor=None, agreement=None):
        amount = Decimal(str(amount))
        return InvestorAllocation.objects.create(
            period=self.period, investor=investor or self.investor, allocation_type=allocation_type, amount=amount,
            capital_component=amount / 2, agreed_component=amount / 2, agreement=agreement or self.agreement,
            proposed_by=self.proposer)

    def approve(self, allocation, approver=None):
        """Allocations are approved while the period is in review. Capital entries need it open, so reopen it after."""
        self.set_period("review")
        try:
            return approve_investor_allocation(allocation_id=allocation.pk, approved_by=approver or self.admin_user)
        finally:
            self.set_period("open")