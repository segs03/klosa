from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase

from funding.models import (
    FundingPeriod,
    LedgerAccount,
    JournalEntry,
    JournalLine,
)
from funding.reversal import reverse_journal_entry



User = get_user_model()


class JournalReversalTests(TestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            username="reversal_admin",
            password="test-password-123",
            is_staff=True,
            is_active=True,
        )

        self.period = FundingPeriod.objects.create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            status="open",
        )

        self.cash = LedgerAccount.objects.create(
            code="1000",
            name="Cash",
            account_type="asset",
        )

        self.capital = LedgerAccount.objects.create(
            code="2000",
            name="Investor capital",
            account_type="equity",
        )

        original = JournalEntry.objects.create(
            reference="ORIGINAL-001",
            period=self.period,
            description="Original journal",
        )

        JournalLine.objects.create(
            entry=original,
            account=self.cash,
            debit=Decimal("5000.00"),
            credit=Decimal("0.00"),
        )

        JournalLine.objects.create(
            entry=original,
            account=self.capital,
            debit=Decimal("0.00"),
            credit=Decimal("5000.00"),
        )

        from funding.accounting import post_journal_entry
        self.original = post_journal_entry(original)

    def test_reversal_creates_opposite_journal(self):
        reversal = reverse_journal_entry(
            journal_id=self.original.pk,
            reversed_by=self.admin,
            reason="Correction of original entry",
            effective_date=date(2026, 1, 20),
            reference="REVERSAL-001",
        )

        self.original.refresh_from_db()

        self.assertEqual(self.original.status, "reversed")
        self.assertEqual(reversal.status, "posted")
        self.assertEqual(reversal.reversal_of_id, self.original.pk)

        lines = list(reversal.lines.all())

        self.assertEqual(len(lines), 2)
        self.assertEqual(
            sum((line.debit for line in lines), Decimal("0.00")),
            Decimal("5000.00"),
        )
        self.assertEqual(
            sum((line.credit for line in lines), Decimal("0.00")),
            Decimal("5000.00"),
        )

    def test_journal_cannot_be_reversed_twice(self):
        reverse_journal_entry(
            journal_id=self.original.pk,
            reversed_by=self.admin,
            reason="First correction",
            effective_date=date(2026, 1, 20),
            reference="REVERSAL-002",
        )

        with self.assertRaises(ValidationError):
            reverse_journal_entry(
                journal_id=self.original.pk,
                reversed_by=self.admin,
                reason="Second correction",
                effective_date=date(2026, 1, 20),
                reference="REVERSAL-003",
            )

    def test_inactive_or_non_staff_user_cannot_reverse(self):
        ordinary_user = User.objects.create_user(
            username="ordinary_user",
            password="test-password-123",
            is_staff=False,
            is_active=True,
        )

        with self.assertRaises(PermissionDenied):
            reverse_journal_entry(
                journal_id=self.original.pk,
                reversed_by=ordinary_user,
                reason="Test",
                effective_date=date(2026, 1, 20),
                reference="REVERSAL-004",
            )

    def test_reversal_requires_reason(self):
        with self.assertRaises(ValidationError):
            reverse_journal_entry(
                journal_id=self.original.pk,
                reversed_by=self.admin,
                reason="   ",
                effective_date=date(2026, 1, 20),
                reference="REVERSAL-005",
            )