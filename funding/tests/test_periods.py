from datetime import date

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError, PermissionDenied

from funding.models import FundingPeriod
from funding.period import (
    start_period_review,
    close_funding_period,
)


User = get_user_model()


class FundingPeriodTests(TestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            username="period_admin",
            password="test-password",
            is_staff=True,
            is_active=True,
        )

        permissions = Permission.objects.filter(
            content_type__app_label="funding",
            codename__in=[
                "review_fundingperiod",
                "close_fundingperiod",
            ],
        )

        self.admin.user_permissions.add(*permissions)

        self.period = FundingPeriod.objects.create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            status="open",
        )

    def test_open_period_can_enter_review(self):
        period = start_period_review(
            period_id=self.period.pk,
            performed_by=self.admin,
        )

        self.assertEqual(period.status, "review")

    def test_only_open_period_can_enter_review(self):
        self.period.status = "closed"
        self.period.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            start_period_review(
                period_id=self.period.pk,
                performed_by=self.admin,
            )

    def test_review_period_can_be_closed(self):
        self.period.status = "review"
        self.period.save(update_fields=["status"])

        period = close_funding_period(
            period_id=self.period.pk,
            performed_by=self.admin,
        )

        self.assertEqual(period.status, "closed")
        self.assertIsNotNone(period.closed_at)

    def test_only_review_period_can_be_closed(self):
        with self.assertRaises(ValidationError):
            close_funding_period(
                period_id=self.period.pk,
                performed_by=self.admin,
            )

    def test_staff_without_permission_cannot_start_review(self):
        user = User.objects.create_user(
            username="other_staff",
            password="test-password",
            is_staff=True,
        )

        with self.assertRaises(PermissionDenied):
            start_period_review(
                period_id=self.period.pk,
                performed_by=user,
            )

    def test_staff_without_permission_cannot_close_period(self):
        user = User.objects.create_user(
            username="other_staff2",
            password="test-password",
            is_staff=True,
        )

        self.period.status = "review"
        self.period.save(update_fields=["status"])

        with self.assertRaises(PermissionDenied):
            close_funding_period(
                period_id=self.period.pk,
                performed_by=user,
            )