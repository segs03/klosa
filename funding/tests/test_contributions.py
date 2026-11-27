from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from funding.contributions import post_verified_investor_contribution
from funding.models import CapitalContribution, FundingPeriod, InvestorCapitalEntry, JournalEntry
from .helpers import FundingBase, staff_with


class InvestorContributionTests(FundingBase):

    def setUp(self):
        super().setUp()
        self.received_at = timezone.now()

    def contribution(self, reference, recorded_by=None, **kw):
        return CapitalContribution.objects.create(
            source_type="investor", investor=self.investor, amount=Decimal("500000.00"), reference=reference,
            received_at=self.received_at, recorded_by=recorded_by or self.proposer, **kw)

    def test_a_second_person_verifies_and_one_ledger_entry_is_made(self):
        c = self.contribution("TEST-CONTRIB-001")
        entry = post_verified_investor_contribution(c.pk, self.admin_user)
        c.refresh_from_db()
        self.assertTrue(c.verified)
        self.assertEqual((c.verified_by, entry.source_contribution_id), (self.admin_user, c.pk))
        self.assertEqual(InvestorCapitalEntry.objects.filter(source_contribution=c).count(), 1)
        lines = entry.journal_entry.lines.all()
        self.assertEqual(sum(l.debit for l in lines), sum(l.credit for l in lines))

    def test_the_recorder_cannot_verify_their_own_entry(self):
        both = staff_with("both_roles", "add_capitalcontribution", "verify_capitalcontribution")
        c = self.contribution("TEST-CONTRIB-OWN", recorded_by=both)
        with self.assertRaises(PermissionDenied):
            post_verified_investor_contribution(c.pk, both)
        self.assertFalse(InvestorCapitalEntry.objects.exists())

    def test_staff_without_the_permission_cannot_verify(self):
        c = self.contribution("TEST-CONTRIB-NOPERM")
        for who in (staff_with("plain_staff"), self.investor_user):
            with self.assertRaises(PermissionDenied):
                post_verified_investor_contribution(c.pk, who)

    def test_an_entry_with_no_recorder_on_record_is_refused(self):
        c = self.contribution("TEST-CONTRIB-NOREC")
        CapitalContribution.objects.filter(pk=c.pk).update(recorded_by=None)
        with self.assertRaises(ValidationError):
            post_verified_investor_contribution(c.pk, self.admin_user)

    def test_already_verified_contribution_is_rejected(self):
        c = self.contribution("TEST-CONTRIB-VERIFIED")
        post_verified_investor_contribution(c.pk, self.admin_user)
        with self.assertRaises(ValidationError):
            post_verified_investor_contribution(c.pk, self.admin_user)

    def test_a_closed_period_refuses_the_contribution(self):
        c = self.contribution("TEST-CONTRIB-CLOSED")
        self.set_period("closed")
        with self.assertRaises(ValidationError):
            post_verified_investor_contribution(c.pk, self.admin_user)

    @patch("funding.contributions.post_journal_entry", side_effect=ValidationError("Simulated journal failure."))
    def test_failed_journal_posting_rolls_back_contribution(self, mock_post):
        c = self.contribution("TEST-CONTRIB-ROLLBACK")
        journals, entries = JournalEntry.objects.count(), InvestorCapitalEntry.objects.count()
        with self.assertRaises(ValidationError):
            post_verified_investor_contribution(c.pk, self.admin_user)
        c.refresh_from_db()
        self.assertFalse(c.verified)
        self.assertIsNone(c.verified_at)
        self.assertEqual((JournalEntry.objects.count(), InvestorCapitalEntry.objects.count()), (journals, entries))
        mock_post.assert_called_once()

