from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from funding.models import (
    FundingPeriod,
    LedgerAccount,
    JournalEntry,
    JournalLine,
)
from funding.accounting import post_journal_entry

from funding.allocation import approve_investor_allocation


class JournalPostingTests(TestCase):

    def setUp(self):
        self.period = FundingPeriod.objects.create(
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 30),
            status="open",
        )

        self.cash = LedgerAccount.objects.create(
            code="1000",
            name="Operating cash",
            account_type="asset",
        )

        self.capital = LedgerAccount.objects.create(
            code="2000",
            name="Investor capital",
            account_type="equity",
        )

    def create_journal(self):
        return JournalEntry.objects.create(
            reference="TEST-JOURNAL-001",
            period=self.period,
            description="Test journal",
            status="draft",
        )

    def test_balanced_journal_can_be_posted(self):
        journal = self.create_journal()

        JournalLine.objects.create(
            entry=journal,
            account=self.cash,
            debit=Decimal("5000.00"),
            credit=Decimal("0.00"),
            description="Cash received",
        )

        JournalLine.objects.create(
            entry=journal,
            account=self.capital,
            debit=Decimal("0.00"),
            credit=Decimal("5000.00"),
            description="Capital received",
        )

        posted = post_journal_entry(journal)

        self.assertEqual(posted.status, "posted")
        self.assertIsNotNone(posted.posted_at)

    def test_unbalanced_journal_is_rejected(self):
        journal = self.create_journal()

        JournalLine.objects.create(
            entry=journal,
            account=self.cash,
            debit=Decimal("5000.00"),
            credit=Decimal("0.00"),
            description="Cash received",
        )

        JournalLine.objects.create(
            entry=journal,
            account=self.capital,
            debit=Decimal("0.00"),
            credit=Decimal("4000.00"),
            description="Capital received",
        )

        with self.assertRaises(ValidationError):
            post_journal_entry(journal)

        journal.refresh_from_db()
        self.assertEqual(journal.status, "draft")

    def test_journal_with_one_line_is_rejected(self):
        journal = self.create_journal()

        JournalLine.objects.create(
            entry=journal,
            account=self.cash,
            debit=Decimal("5000.00"),
            credit=Decimal("0.00"),
            description="Incomplete journal",
        )

        with self.assertRaises(ValidationError):
            post_journal_entry(journal)

        journal.refresh_from_db()
        self.assertEqual(journal.status, "draft")