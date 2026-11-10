
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from funding.models import (
    Investor,
    CapitalContribution,
    FundingPeriod,
    LedgerAccount,
    InvestorCapitalEntry,
    JournalEntry
)
from funding.contributions import (
    post_verified_investor_contribution,
)
from unittest.mock import patch



User = get_user_model()


class InvestorContributionTests(TestCase):

    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="funding_admin",
            password="test-password-123",
        )

        self.investor_user = User.objects.create_user(
            username="investor_test",
            password="test-password-123",
        )

        self.investor = Investor.objects.create(
            user=self.investor_user,
            legal_name="Test Investor",
        )

        self.received_at = timezone.now()

        FundingPeriod.objects.create(
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 30),
            status="open",
        )

        LedgerAccount.objects.create(
            code="1000",
            name="Operating cash",
            account_type="asset",
        )

        LedgerAccount.objects.create(
            code="2000",
            name="Investor capital",
            account_type="equity",
        )

        self.verified_contribution = CapitalContribution.objects.create(
            source_type="investor",
            investor=self.investor,
            amount=Decimal("500000.00"),
            reference="TEST-CONTRIB-VERIFIED",
            received_at=self.received_at,
            verified=True,
            verified_by=self.admin_user,
            verified_at=self.received_at,
        )
        
    @patch(
        "funding.accounting.post_journal_entry",
        side_effect=ValidationError("Simulated journal failure."),
    )
    
    def test_failed_journal_posting_rolls_back_contribution(
        self,
        mock_post,
    ):
        contribution = CapitalContribution.objects.create(
            source_type="investor",
            investor=self.investor,
            amount=Decimal("500000.00"),
            reference="TEST-CONTRIB-ROLLBACK",
            received_at=self.received_at,
            verified=False,
        )

        journal_count_before = JournalEntry.objects.count()
        capital_count_before = InvestorCapitalEntry.objects.count()

        with self.assertRaises(ValidationError):
            post_verified_investor_contribution(
                contribution.pk,
                self.admin_user,
            )

        contribution.refresh_from_db()

        self.assertFalse(contribution.verified)
        self.assertIsNone(contribution.verified_at)
        self.assertEqual(
            JournalEntry.objects.count(),
            journal_count_before,
        )
        self.assertEqual(
            InvestorCapitalEntry.objects.count(),
            capital_count_before,
        )

        mock_post.assert_called_once()

    def test_contribution_creates_one_ledger_entry(self):
        contribution = CapitalContribution.objects.create(
            source_type="investor",
            investor=self.investor,
            amount=Decimal("500000.00"),
            reference="TEST-CONTRIB-001",
            received_at=self.received_at,
            verified=False,
        )

        entry = post_verified_investor_contribution(
            contribution.pk,
            self.admin_user,
        )

        contribution.refresh_from_db()

        self.assertTrue(contribution.verified)
        self.assertEqual(contribution.verified_by, self.admin_user)
        self.assertEqual(
            entry.source_contribution_id,
            contribution.pk,
        )

        self.assertEqual(
            InvestorCapitalEntry.objects.filter(
                source_contribution=contribution
            ).count(),
            1,
        )

    def test_already_verified_contribution_is_rejected(self):
        with self.assertRaises(ValidationError):
            post_verified_investor_contribution(
                self.verified_contribution.pk,
                self.admin_user,
            )
