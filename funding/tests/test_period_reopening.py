from datetime import date
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError, PermissionDenied
from django.utils import timezone

from funding.models import FundingPeriod, FundingPeriodReopening
from funding.period_reopening import reopen_funding_period


User = get_user_model()


class FundingPeriodReopeningTests(TestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            username="period_admin",
            password="test-password",
            is_staff=True,
            is_active=True,
        )

        permission = Permission.objects.get(
            content_type__app_label="funding",
            codename="reopen_fundingperiod",
        )
        self.admin.user_permissions.add(permission)

        self.period = FundingPeriod.objects.create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            status="closed",
            closed_at=timezone.now(),
        )

    def test_authorised_user_can_reopen_period(self):
        previous_closed_at = self.period.closed_at

        period = reopen_funding_period(
            period_id=self.period.pk,
            reopened_by=self.admin,
            reason="Correcting an accounting classification.",
        )

        self.assertEqual(period.status, "open")
        self.assertIsNone(period.closed_at)

        audit = FundingPeriodReopening.objects.get(
            period=period
        )
        self.assertEqual(audit.reopened_by, self.admin)
        self.assertEqual(audit.previous_closed_at, previous_closed_at)

    def test_reopening_requires_permission(self):
        user = User.objects.create_user(
            username="unauthorised",
            password="test-password",
            is_staff=True,
        )

        with self.assertRaises(PermissionDenied):
            reopen_funding_period(
                period_id=self.period.pk,
                reopened_by=user,
                reason="Test reason",
            )

    def test_reopening_requires_reason(self):
        with self.assertRaises(ValidationError):
            reopen_funding_period(
                period_id=self.period.pk,
                reopened_by=self.admin,
                reason="   ",
            )

    def test_only_closed_period_can_be_reopened(self):
        self.period.status = "open"
        self.period.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            reopen_funding_period(
                period_id=self.period.pk,
                reopened_by=self.admin,
                reason="Test reason",
            )

    def test_reopening_creates_audit_record(self):
        reopen_funding_period(
            period_id=self.period.pk,
            reopened_by=self.admin,
            reason="Audit test.",
        )

        self.assertEqual(
            FundingPeriodReopening.objects.filter(
                period=self.period
            ).count(),
            1,
        )