"""Tests for funding/contributor.py: notices, confirming, rejecting and posting to the ledger.

need() and audit_log() are replaced with mocks so these tests check the money and ledger rules,
not your permission setup. Permission checks are asserted by what need() was called with.
"""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.tests.helpers import make_user
from funding import contributor
from funding.contributor import (_amount, confirm_received, create_intent, reject_intent,
                                 verify_contribution)
from funding.models import (CapitalContribution, ContributionIntent, FundingPeriod, Investor,
                            InvestorCapitalEntry, JournalEntry, LedgerAccount, LedgerPosting)

CONFIRM_PERM = "funding.confirm_contributionintent"


class AmountTests(TestCase):
    def test_good_amounts_are_returned_to_two_places(self):
        self.assertEqual(_amount("10000"), Decimal("10000.00"))
        self.assertEqual(_amount(" 25000.50 "), Decimal("25000.50"))
        self.assertEqual(_amount(1000000000), Decimal("1000000000.00"))

    def test_bad_amounts_are_refused(self):
        for bad in ("9999.99", "1000000001", "10000.001", "abc", "", "NaN", "Infinity", "10,000", None):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                _amount(bad)


class FlowBase(TestCase):
    def setUp(self):
        for name in ("need", "audit_log"):
            p = mock.patch(f"funding.contributor.{name}")
            setattr(self, name, p.start())
            self.addCleanup(p.stop)
        self.user = make_user("investor")
        self.investor = Investor.objects.create(user=self.user, legal_name="Ada Obi")
        self.today = timezone.localdate()
        self.period = FundingPeriod.objects.create(
            start_date=self.today - timedelta(days=5), end_date=self.today + timedelta(days=25))
        for code, name, kind in (("1000", "Operating cash", "asset"), ("2000", "Investor capital", "liability"),
                                 ("2100", "Company capital", "liability")):
            LedgerAccount.objects.get_or_create(code=code, defaults={"name": name, "account_type": kind})
        self.recorder = make_user("recorder")
        self.verifier = make_user("verifier")

    def intent(self, amount="50000", method="bank_transfer"):
        return create_intent(self.user, amount, method)

    def received(self):
        return confirm_received(self.intent().pk, self.recorder).contribution


class CreateIntentTests(FlowBase):
    def test_an_intent_is_created_pending_with_a_reference_and_expiry(self):
        i = self.intent("25000.50")
        self.assertEqual((i.status, i.amount, i.investor), ("pending", Decimal("25000.50"), self.investor))
        self.assertTrue(i.reference.startswith("IN"))
        self.assertLess(abs((i.expires_at - (timezone.now() + timedelta(days=7))).total_seconds()), 10)

    def test_references_are_unique(self):
        self.assertEqual(len({self.intent().reference for _ in range(3)}), 3)

    def test_three_open_notices_is_the_limit(self):
        for _ in range(3):
            self.intent()
        with self.assertRaisesMessage(ValidationError, "3 open deposit notices"):
            self.intent()

    def test_expired_notices_do_not_count_toward_the_limit(self):
        first = self.intent()
        self.intent()
        self.intent()
        ContributionIntent.objects.filter(pk=first.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertTrue(self.intent())
        first.refresh_from_db()
        self.assertEqual(first.status, "expired")

    def test_handled_notices_do_not_count_toward_the_limit(self):
        for _ in range(3):
            reject_intent(self.intent().pk, self.recorder, "Funds never arrived")
        self.assertTrue(self.intent())

    def test_bad_input_is_refused(self):
        with self.assertRaises(ValidationError):
            self.intent(amount="5")
        with self.assertRaisesMessage(ValidationError, "how you will pay"):
            self.intent(method="crypto")
        self.assertFalse(ContributionIntent.objects.exists())

    def test_inactive_investors_and_non_investors_are_refused(self):
        with self.assertRaisesMessage(ValidationError, "not an active investor"):
            create_intent(make_user("nobody"), "50000", "cash")
        Investor.objects.filter(pk=self.investor.pk).update(is_active=False)
        with self.assertRaisesMessage(ValidationError, "not an active investor"):
            self.intent()


class ConfirmReceivedTests(FlowBase):
    def test_confirming_records_an_unverified_contribution(self):
        i = self.intent()
        done = confirm_received(i.pk, self.recorder)
        c = done.contribution
        self.assertEqual((c.source_type, c.investor, c.amount, c.reference),
                         ("investor", self.investor, i.amount, i.reference))
        self.assertEqual((c.verified, c.recorded_by), (False, self.recorder))
        self.assertEqual((done.status, done.handled_by), ("received", self.recorder))
        self.assertIsNotNone(done.handled_at)

    def test_the_permission_is_checked_and_the_audit_trail_written(self):
        i = self.intent()
        confirm_received(i.pk, self.recorder)
        self.need.assert_called_with(self.recorder, CONFIRM_PERM)
        self.audit_log.assert_called_with(self.recorder, "intent_confirmed", i.reference, "50000.00")   # as read back from the database

    def test_nothing_is_posted_to_the_ledger_yet(self):
        self.received()
        self.assertFalse(JournalEntry.objects.exists())
        self.assertFalse(InvestorCapitalEntry.objects.exists())

    def test_a_notice_can_only_be_confirmed_once(self):
        i = self.intent()
        confirm_received(i.pk, self.recorder)
        with self.assertRaisesMessage(ValidationError, "already handled"):
            confirm_received(i.pk, self.recorder)
        self.assertEqual(CapitalContribution.objects.count(), 1)

    def test_late_money_on_an_expired_notice_is_still_accepted(self):
        i = self.intent()
        ContributionIntent.objects.filter(pk=i.pk).update(status="expired", expires_at=timezone.now() - timedelta(days=1))
        self.assertEqual(confirm_received(i.pk, self.recorder).status, "received")

    def test_nobody_confirms_their_own_deposit(self):
        i = self.intent()
        with self.assertRaises(PermissionDenied):
            confirm_received(i.pk, self.user)
        self.assertFalse(CapitalContribution.objects.exists())

    def test_an_inactive_investor_and_an_unknown_notice_are_refused(self):
        i = self.intent()
        Investor.objects.filter(pk=self.investor.pk).update(is_active=False)
        with self.assertRaisesMessage(ValidationError, "not active"):
            confirm_received(i.pk, self.recorder)
        with self.assertRaisesMessage(ValidationError, "not found"):
            confirm_received(999999, self.recorder)


class RejectIntentTests(FlowBase):
    def test_rejecting_records_the_reason(self):
        i = self.intent()
        done = reject_intent(i.pk, self.recorder, "  Funds   never arrived ")
        self.assertEqual((done.status, done.note, done.handled_by), ("rejected", "Funds never arrived", self.recorder))
        self.need.assert_called_with(self.recorder, CONFIRM_PERM)

    def test_a_reason_is_required(self):
        with self.assertRaisesMessage(ValidationError, "Give a reason"):
            reject_intent(self.intent().pk, self.recorder, "   ")

    def test_a_handled_notice_cannot_be_rejected_or_confirmed(self):
        i = self.intent()
        reject_intent(i.pk, self.recorder, "No funds")
        with self.assertRaisesMessage(ValidationError, "already handled"):
            reject_intent(i.pk, self.recorder, "Again")
        with self.assertRaisesMessage(ValidationError, "already handled"):
            confirm_received(i.pk, self.recorder)

    def test_an_expired_notice_can_still_be_rejected(self):
        i = self.intent()
        ContributionIntent.objects.filter(pk=i.pk).update(status="expired")
        self.assertEqual(reject_intent(i.pk, self.recorder, "Never paid").status, "rejected")


class VerifyContributionTests(FlowBase):
    def test_verifying_posts_a_balanced_journal_and_a_capital_entry(self):
        c = self.received()
        entry = verify_contribution(c.pk, self.verifier)
        entry.refresh_from_db()
        lines = {l.account.code: l for l in entry.lines.select_related("account")}
        self.assertEqual(entry.status, "posted")
        self.assertEqual((lines["1000"].debit, lines["1000"].credit), (c.amount, Decimal("0")))
        self.assertEqual((lines["2000"].debit, lines["2000"].credit), (Decimal("0"), c.amount))
        cap = InvestorCapitalEntry.objects.get(source_contribution=c)
        self.assertEqual((cap.investor, cap.entry_type, cap.amount, cap.effective_date, cap.journal_entry),
                         (self.investor, "contribution", c.amount, self.today, entry))
        self.assertIsNotNone(cap.posted_at)

    def test_the_contribution_is_marked_verified_and_linked(self):
        c = self.received()
        entry = verify_contribution(c.pk, self.verifier)
        c.refresh_from_db()
        self.assertEqual((c.verified, c.verified_by, c.journal_reference), (True, self.verifier, entry.reference))
        self.assertIsNotNone(c.verified_at)
        self.assertEqual(LedgerPosting.objects.get(source_type="contribution", source_id=c.pk).entry, entry)
        self.need.assert_called_with(self.verifier, contributor.PERM_VERIFY)
        self.audit_log.assert_called_with(self.verifier, "contribution_verified", c.reference, entry.reference)

    def test_the_person_who_recorded_it_cannot_verify_it(self):
        c = self.received()
        with self.assertRaises(PermissionDenied):
            verify_contribution(c.pk, self.recorder)
        c.refresh_from_db()
        self.assertFalse(c.verified)

    def test_the_investor_cannot_verify_their_own_deposit(self):
        c = self.received()
        with self.assertRaises(PermissionDenied):
            verify_contribution(c.pk, self.user)

    def test_it_cannot_be_verified_twice_or_found_twice(self):
        c = self.received()
        verify_contribution(c.pk, self.verifier)
        with self.assertRaisesMessage(ValidationError, "already verified"):
            verify_contribution(c.pk, self.verifier)
        with self.assertRaisesMessage(ValidationError, "not found"):
            verify_contribution(999999, self.verifier)
        self.assertEqual(JournalEntry.objects.count(), 1)

    def test_an_existing_posting_blocks_double_posting(self):
        c = self.received()
        LedgerPosting.objects.create(source_type="contribution", source_id=c.pk)
        with self.assertRaisesMessage(ValidationError, "already been posted"):
            verify_contribution(c.pk, self.verifier)
        self.assertFalse(JournalEntry.objects.exists())

    def test_a_closed_or_missing_period_blocks_posting_and_rolls_everything_back(self):
        c = self.received()
        for change in ({"status": "closed"}, {"status": "review"}):
            FundingPeriod.objects.filter(pk=self.period.pk).update(**change)
            with self.subTest(change=change), self.assertRaisesMessage(ValidationError, "No open funding period"):
                verify_contribution(c.pk, self.verifier)
        FundingPeriod.objects.filter(pk=self.period.pk).update(
            status="open", start_date=self.today + timedelta(days=2), end_date=self.today + timedelta(days=9))
        with self.assertRaisesMessage(ValidationError, "No open funding period"):
            verify_contribution(c.pk, self.verifier)
        c.refresh_from_db()
        self.assertFalse(c.verified)
        self.assertFalse(JournalEntry.objects.exists())
        self.assertFalse(LedgerPosting.objects.exists())

    def test_a_missing_ledger_account_stops_the_posting(self):
        c = self.received()
        LedgerAccount.objects.filter(code="1000").delete()
        with self.assertRaises(LedgerAccount.DoesNotExist):
            verify_contribution(c.pk, self.verifier)
        self.assertFalse(JournalEntry.objects.exists())

    def test_company_capital_goes_to_its_own_account_with_no_investor_entry(self):
        c = CapitalContribution.objects.create(
            source_type="company", amount=Decimal("75000"), reference="CO-1",
            received_at=timezone.now(), recorded_by=self.recorder)
        entry = verify_contribution(c.pk, self.verifier)
        self.assertEqual({l.account.code for l in entry.lines.select_related("account")}, {"1000", "2100"})
        self.assertFalse(InvestorCapitalEntry.objects.exists())


