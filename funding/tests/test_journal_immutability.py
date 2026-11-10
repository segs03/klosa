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


class JournalImmutabilityTests(TestCase):

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

        self.journal = JournalEntry.objects.create(
            reference="IMMUTABLE-001",
            period=self.period,
            description="Test journal",
            status="draft",
        )

        self.debit_line = JournalLine.objects.create(
            entry=self.journal,
            account=self.cash,
            debit=Decimal("5000.00"),
            credit=Decimal("0.00"),
            description="Cash received",
        )

        self.credit_line = JournalLine.objects.create(
            entry=self.journal,
            account=self.capital,
            debit=Decimal("0.00"),
            credit=Decimal("5000.00"),
            description="Capital received",
        )

        # Simulate an already-posted journal.
        self.journal.status = "posted"
        self.journal.save = JournalEntry.save.__get__(
            self.journal, JournalEntry
        )
        JournalEntry.objects.filter(pk=self.journal.pk).update(
            status="posted"
        )

    def test_posted_journal_cannot_be_edited(self):
        with self.assertRaises(ValidationError):
            self.journal.description = "Changed description"
            self.journal.save()

    def test_posted_journal_cannot_be_deleted(self):
        with self.assertRaises(ValidationError):
            self.journal.delete()

    def test_posted_journal_line_cannot_be_edited(self):
        with self.assertRaises(ValidationError):
            self.debit_line.description = "Changed line"
            self.debit_line.save()

    def test_posted_journal_line_cannot_be_deleted(self):
        with self.assertRaises(ValidationError):
            self.debit_line.delete()

    def test_posted_journal_cannot_receive_new_lines(self):
        with self.assertRaises(ValidationError):
            JournalLine.objects.create(
                entry=self.journal,
                account=self.cash,
                debit=Decimal("100.00"),
                credit=Decimal("0.00"),
                description="New line",
            )