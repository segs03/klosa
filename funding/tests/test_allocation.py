import calendar
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.utils import timezone

from funding.models import (
    Investor,
    InvestorAgreement,
    InvestorAllocation,
    FundingPeriod,
)
from funding.allocation import approve_investor_allocation

from funding.accounting import post_investor_capital_event

from django.contrib.auth.models import Permission





User = get_user_model()


class InvestorAllocationApprovalTests(TestCase):

    def setUp(self):
        self.today = timezone.localdate()

        self.investor_user = User.objects.create_user(
            username="allocation_investor",
            password="test-password-123",
        )

        self.admin_user = User.objects.create_user(
            username="allocation_admin",
            password="test-password-123",
            is_staff=True,
            is_active=True,
        )
        
        permission = Permission.objects.get(
    content_type__app_label="funding",
    codename="approve_investorallocation",
)

        self.admin_user.user_permissions.add(permission)

        self.inactive_admin = User.objects.create_user(
            username="inactive_admin",
            password="test-password-123",
            is_staff=True,
            is_active=False,
        )

        self.investor = Investor.objects.create(
            user=self.investor_user,
            legal_name="Allocation Test Investor",
        )

        first_day = self.today.replace(day=1)
        last_day = self.today.replace(
            day=calendar.monthrange(
                self.today.year,
                self.today.month,
            )[1]
        )

        self.period = FundingPeriod.objects.create(
            start_date=first_day,
            end_date=last_day,
            status="open",
        )

        self.agreement = InvestorAgreement.objects.create(
            investor=self.investor,
            capital_basis="time_weighted_average",
            profit_treatment="retain",
            effective_from=first_day,
            profit_capital_component=Decimal("50.00"),
            profit_agreed_component=Decimal("50.00"),
            agreed_profit_share=Decimal("100.00"),
            loss_capital_component=Decimal("50.00"),
            loss_agreed_component=Decimal("50.00"),
            agreed_loss_share=Decimal("100.00"),
            approved=True,
        )

    def create_allocation(self):
        return InvestorAllocation.objects.create(
            period=self.period,
            investor=self.investor,
            allocation_type="profit",
            amount=Decimal("2000.00"),
            capital_component=Decimal("1000.00"),
            agreed_component=Decimal("1000.00"),
            agreement=self.agreement,
        )
        
    def test_staff_without_approval_permission_cannot_approve(self):
        allocation = self.create_allocation()

        ordinary_staff = User.objects.create_user(
        username="ordinary_staff",
        password="test-password-123",
        is_staff=True,
        is_active=True,
    )

        with self.assertRaises(PermissionDenied):
            approve_investor_allocation(
            allocation_id=allocation.pk,
            approved_by=ordinary_staff,
        )
        
    def test_approved_allocation_cannot_be_edited(self):
        allocation = self.create_allocation()

        approve_investor_allocation(
        allocation_id=allocation.pk,
        approved_by=self.admin_user,
    )

        allocation.refresh_from_db()
        allocation.amount += Decimal("100.00")

        with self.assertRaises(ValidationError):
            allocation.save()


    def test_approved_allocation_cannot_be_deleted(self):
        allocation = self.create_allocation()

        approve_investor_allocation(
        allocation_id=allocation.pk,
        approved_by=self.admin_user,
    )

        with self.assertRaises(ValidationError):
            allocation.delete()

            self.assertTrue(
        InvestorAllocation.objects.filter(pk=allocation.pk).exists()
    )

    def test_investor_cannot_approve_allocation(self):
        allocation = self.create_allocation()

        with self.assertRaises(PermissionDenied):
            approve_investor_allocation(
                allocation_id=allocation.pk,
                approved_by=self.investor_user,
            )

    def test_inactive_admin_cannot_approve_allocation(self):
        allocation = self.create_allocation()

        with self.assertRaises(PermissionDenied):
            approve_investor_allocation(
                allocation_id=allocation.pk,
                approved_by=self.inactive_admin,
            )

    def test_allocation_cannot_be_approved_twice(self):
        allocation = self.create_allocation()

        approve_investor_allocation(
            allocation_id=allocation.pk,
            approved_by=self.admin_user,
        )

        with self.assertRaises(ValidationError):
            approve_investor_allocation(
                allocation_id=allocation.pk,
                approved_by=self.admin_user,
            )

    def test_unapproved_allocation_cannot_be_posted(self):
        allocation = self.create_allocation()

        with self.assertRaises(ValidationError):
            post_investor_capital_event(
                investor_id=self.investor.pk,
                entry_type="retained_profit",
                amount=Decimal("2000.00"),
                effective_date=self.today,
                reference="UNAPPROVED-PROFIT-001",
                allocation=allocation,
            )