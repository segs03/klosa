from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError

from funding.accounting import post_investor_capital_event
from funding.capital import capital_balance, retained_profit_balance
from funding.models import Investor, InvestorCapitalEntry
from .helpers import FundingBase, staff_with

User = get_user_model()


class InvestorCapitalTests(FundingBase):

    def post(self, entry_type, amount, reference, allocation=None, **kw):
        return post_investor_capital_event(
            posted_by=kw.pop("posted_by", self.admin_user), investor_id=self.investor.pk, entry_type=entry_type,
            amount=amount, effective_date=kw.pop("effective_date", self.today), reference=reference,
            allocation=allocation, **kw)

    def assert_journal_mapping(self, entry_type, amount, debit_code, credit_code, reference, allocation=None):
        entry = self.post(entry_type, amount, reference, allocation)
        lines = list(entry.journal_entry.lines.select_related("account"))
        self.assertEqual(len(lines), 2)
        debits = [l for l in lines if l.debit > 0]
        credits = [l for l in lines if l.credit > 0]
        self.assertEqual((len(debits), len(credits)), (1, 1))
        self.assertEqual((debits[0].account.code, credits[0].account.code), (debit_code, credit_code))
        self.assertEqual((entry.journal_entry.status, entry.amount), ("posted", amount))
        return entry

    # ------------------------------------------------------------ journal mapping
    def test_contribution_journal_mapping(self):
        self.assert_journal_mapping("contribution", Decimal("1000.00"), "1000", "2000", "CONTRIB-001")

    def test_withdrawal_journal_mapping(self):
        self.assert_journal_mapping("contribution", Decimal("5000.00"), "1000", "2000", "CONTRIB-002")
        self.assert_journal_mapping("withdrawal", Decimal("1000.00"), "2000", "1000", "WITHDRAW-001")

    def test_retained_profit_journal_mapping(self):
        allocation = self.approve(self.create_allocation("profit", "2000.00"))
        self.assert_journal_mapping("retained_profit", Decimal("2000.00"), "3000", "2200", "PROFIT-001", allocation)

    def test_reinvestment_journal_mapping(self):
        allocation = self.approve(self.create_allocation("profit", "2000.00"))
        self.post("retained_profit", Decimal("2000.00"), "PROFIT-002", allocation)
        self.assert_journal_mapping("reinvestment", Decimal("1000.00"), "2200", "2000", "REINVEST-001")
        self.assertEqual(retained_profit_balance(self.investor), Decimal("1000.00"))
        self.assertEqual(capital_balance(self.investor), Decimal("1000.00"))

    def test_payout_journal_mapping(self):
        allocation = self.approve(self.create_allocation("profit", "2000.00"))
        self.post("retained_profit", Decimal("2000.00"), "PROFIT-003", allocation)
        self.assert_journal_mapping("payout", Decimal("1000.00"), "2200", "1000", "PAYOUT-001")

    def test_loss_journal_mapping(self):
        self.post("contribution", Decimal("5000.00"), "CONTRIB-LOSS-001")
        allocation = self.approve(self.create_allocation("loss", "1000.00"))
        self.assert_journal_mapping("loss", Decimal("1000.00"), "2000", "3100", "LOSS-001", allocation)

    # ---------------------------------------------------------------- balances
    def test_retained_profit_is_not_investment_capital(self):
        allocation = self.approve(self.create_allocation("profit", "100000.00"))
        self.post("retained_profit", Decimal("100000.00"), "RETAINED-001", allocation)
        self.assertEqual(retained_profit_balance(self.investor), Decimal("100000.00"))
        self.assertEqual(capital_balance(self.investor), Decimal("0.00"))

    def test_reinvestment_moves_retained_profit_into_capital(self):
        allocation = self.approve(self.create_allocation("profit", "100000.00"))
        self.post("retained_profit", Decimal("100000.00"), "RETAINED-002", allocation)
        self.post("reinvestment", Decimal("40000.00"), "REINVEST-002")
        self.assertEqual(retained_profit_balance(self.investor), Decimal("60000.00"))
        self.assertEqual(capital_balance(self.investor), Decimal("40000.00"))

    def test_reinvestment_cannot_exceed_retained_profit(self):
        allocation = self.approve(self.create_allocation("profit", "30000.00"))
        self.post("retained_profit", Decimal("30000.00"), "RETAINED-003", allocation)
        with self.assertRaises(ValidationError):
            self.post("reinvestment", Decimal("40000.00"), "REINVEST-003")
        self.assertEqual(capital_balance(self.investor), Decimal("0.00"))

    def test_withdrawal_cannot_exceed_investment_capital(self):
        self.post("contribution", Decimal("50000.00"), "CONTRIBUTION-001")
        with self.assertRaises(ValidationError):
            self.post("withdrawal", Decimal("60000.00"), "WITHDRAWAL-001")
        self.assertEqual(capital_balance(self.investor), Decimal("50000.00"))

    # ------------------------------------------------------------- allocations
    def test_unapproved_allocation_cannot_be_posted(self):
        allocation = self.create_allocation("profit", "2000.00")
        with self.assertRaises(ValidationError):
            self.post("retained_profit", Decimal("2000.00"), "UNAPPROVED-ENTRY", allocation)
        self.assertFalse(InvestorCapitalEntry.objects.filter(reference="UNAPPROVED-ENTRY").exists())

    def test_retained_profit_requires_an_allocation(self):
        with self.assertRaises(ValidationError):
            self.post("retained_profit", Decimal("2000.00"), "MISSING-ALLOCATION-ENTRY")

    def test_allocation_amount_must_match_the_entry(self):
        allocation = self.approve(self.create_allocation("profit", "2000.00"))
        with self.assertRaises(ValidationError):
            self.post("retained_profit", Decimal("1999.00"), "WRONG-AMOUNT-ENTRY", allocation)

    def test_allocation_from_another_investor_is_rejected(self):
        other = Investor.objects.create(user=User.objects.create_user("other_investor", password="x"), legal_name="Other")
        agreement = self.make_agreement(other, self.today.replace(day=1))
        allocation = self.approve(self.create_allocation("profit", "2000.00", investor=other, agreement=agreement))
        with self.assertRaises(ValidationError):
            self.post("retained_profit", Decimal("2000.00"), "OTHER-INVESTOR-ENTRY", allocation)

    def test_allocation_cannot_be_reused(self):
        allocation = self.approve(self.create_allocation("profit", "2000.00"))
        self.post("retained_profit", Decimal("2000.00"), "ALLOCATION-FIRST-ENTRY", allocation)
        with self.assertRaises(ValidationError):
            self.post("retained_profit", Decimal("2000.00"), "ALLOCATION-SECOND-ENTRY", allocation)

    # ------------------------------------------------------------------ rules
    def test_posting_needs_the_permission(self):
        with self.assertRaises(PermissionDenied):
            self.post("contribution", Decimal("100.00"), "NOPERM-001", posted_by=staff_with("no_perms"))
        with self.assertRaises(PermissionDenied):
            self.post("contribution", Decimal("100.00"), "NOPERM-002", posted_by=self.investor_user)
        self.assertFalse(InvestorCapitalEntry.objects.exists())

    def test_the_reference_cannot_be_used_twice(self):
        self.post("contribution", Decimal("100.00"), "DUP-001")
        with self.assertRaises(ValidationError):
            self.post("contribution", Decimal("100.00"), "DUP-001")
        self.assertEqual(InvestorCapitalEntry.objects.count(), 1)

    def test_posting_needs_exactly_one_open_period(self):
        self.set_period("closed")
        with self.assertRaises(ValidationError):
            self.post("contribution", Decimal("100.00"), "CLOSED-001")
        self.set_period("open")
        with self.assertRaises(ValidationError):
            self.post("contribution", Decimal("100.00"), "OUTSIDE-001",
                      effective_date=self.period.end_date.replace(year=self.period.end_date.year + 1))

    def test_bad_amounts_are_refused(self):
        for amount in ("0", "-5", "abc"):
            with self.assertRaises((ValidationError, ArithmeticError)):
                self.post("contribution", amount, f"BAD-{amount}")
        self.assertFalse(InvestorCapitalEntry.objects.exists())