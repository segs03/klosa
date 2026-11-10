
from datetime import date
from decimal import Decimal
import calendar

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from funding.models import (
    Investor,
    InvestorCapitalEntry,
    FundingPeriod,
    LedgerAccount,
    JournalEntry,
    JournalLine,
    InvestorAgreement,
    InvestorAllocation,
    ZERO,
)
from funding.capital import (
    capital_balance,
    retained_profit_balance,
    post_capital_entry,
)
from funding.accounting import post_journal_entry, post_investor_capital_event

from funding.allocation import approve_investor_allocation

from django.contrib.auth.models import Permission


User = get_user_model()


class InvestorCapitalTests(TestCase):

    def setUp(self):
        user = User.objects.create_user(
            username="capital_test",
            password="test-password-123",
        )
        
        self.admin_user = User.objects.create_user(
    username="klosa_admin",
    password="test-password-123",
    is_staff=True,
    is_active=True,
)

        permission = Permission.objects.get(
    content_type__app_label="funding",
    codename="approve_investorallocation",
)

        self.admin_user.user_permissions.add(permission)

        self.investor = Investor.objects.create(
            user=user,
            legal_name="Capital Test Investor",
        )
        
        self.agreement = InvestorAgreement.objects.create(
    investor=self.investor,
    capital_basis="time_weighted_average",
    profit_treatment="retain",
    effective_from=timezone.localdate(),
    profit_capital_component=Decimal("50.00"),
    profit_agreed_component=Decimal("50.00"),
    agreed_profit_share=Decimal("100.00"),
    loss_capital_component=Decimal("50.00"),
    loss_agreed_component=Decimal("50.00"),
    agreed_loss_share=Decimal("100.00"),
    approved=True,
)

        self.today = timezone.localdate()

        first_day = self.today.replace(day=1)
        last_day = self.today.replace(
            day=calendar.monthrange(
                self.today.year, self.today.month
            )[1]
        )

        self.period = FundingPeriod.objects.create(
            start_date=first_day,
            end_date=last_day,
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
        
        self.retained_allocations = LedgerAccount.objects.create(
    code="2200",
    name="Retained allocations",
    account_type="equity",
)

        self.retained_earnings = LedgerAccount.objects.create(
    code="3000",
    name="Retained earnings",
    account_type="equity",
)

        self.loss_clearing = LedgerAccount.objects.create(
    code="3100",
    name="Loss clearing",
    account_type="equity",
)


    def create_mapped_journal(
    self, reference, amount, debit_account, credit_account
):
        amount = Decimal(str(amount))

        journal = JournalEntry.objects.create(
        reference=f"JRN-{reference}",
        period=self.period,
        description="Allocation validation test",
        status="draft",
    )

        JournalLine.objects.create(
        entry=journal,
        account=debit_account,
        debit=amount,
        credit=ZERO,
        description="Test debit",
    )

        JournalLine.objects.create(
        entry=journal,
        account=credit_account,
        debit=ZERO,
        credit=amount,
        description="Test credit",
    )

        return post_journal_entry(journal)


    def assert_journal_mapping(
    self, entry_type, amount, expected_debit, expected_credit,
    reference, allocation=None,
):
        entry = post_investor_capital_event(
        investor_id=self.investor.pk,
        entry_type=entry_type,
        amount=amount,
        effective_date=self.today,
        reference=reference,
        allocation=allocation,
    )

        lines = list(
        entry.journal_entry.lines.select_related("account")
    )

        self.assertEqual(len(lines), 2)

        debit_lines = [line for line in lines if line.debit > 0]
        credit_lines = [line for line in lines if line.credit > 0]

        self.assertEqual(len(debit_lines), 1)
        self.assertEqual(len(credit_lines), 1)

        self.assertEqual(debit_lines[0].account.code, expected_debit)
        self.assertEqual(credit_lines[0].account.code, expected_credit)

        self.assertEqual(entry.journal_entry.status, "posted")
        self.assertEqual(entry.amount, amount)
        
    def create_allocation(self, allocation_type, amount):
        
        return InvestorAllocation.objects.create(
        period=self.period,
        investor=self.investor,
        allocation_type=allocation_type,
        amount=amount,
        capital_component=amount / 2,
        agreed_component=amount / 2,
        agreement=self.agreement,
    )
    
    
    def test_retained_profit_journal_mapping(self):
        amount = Decimal("2000.00")
        allocation = self.create_allocation("profit", amount)

        allocation = approve_investor_allocation(
    allocation_id=allocation.pk,
    approved_by=self.admin_user,
)

        self.assert_journal_mapping(
        "retained_profit",
        amount,
        "3000",
        "2200",
        "PROFIT-001",
        allocation=allocation,
    )


    def test_reinvestment_journal_mapping(self):
        profit = Decimal("2000.00")
        allocation = self.create_allocation("profit", profit)

    # Approve the allocation first.
        allocation = approve_investor_allocation(
        allocation_id=allocation.pk,
        approved_by=self.admin_user,
    )

    # Record the retained profit.
        post_investor_capital_event(
        investor_id=self.investor.pk,
        entry_type="retained_profit",
        amount=profit,
        effective_date=self.today,
        reference="PROFIT-002",
        allocation=allocation,
    )

    # Reinvest part of the retained profit.
        self.assert_journal_mapping(
        "reinvestment",
        Decimal("1000.00"),
        "2200",
        "2000",
        "REINVEST-001",
    )

    # Verify the remaining balances.
        self.assertEqual(
        retained_profit_balance(self.investor),
        Decimal("1000.00"),
    )

        self.assertEqual(
        capital_balance(self.investor),
        Decimal("1000.00"),
    )


    def test_payout_journal_mapping(self):
        profit = Decimal("2000.00")
        allocation = self.create_allocation("profit", profit)
        
        allocation = approve_investor_allocation(
    allocation_id=allocation.pk,
    approved_by=self.admin_user,
)

        post_investor_capital_event(
        investor_id=self.investor.pk,
        entry_type="retained_profit",
        amount=profit,
        effective_date=self.today,
        reference="PROFIT-003",
        allocation=allocation,
    )

        self.assert_journal_mapping(
        "payout",
        Decimal("1000.00"),
        "2200",
        "1000",
        "PAYOUT-001",
    )


    def test_loss_journal_mapping(self):
        capital = Decimal("5000.00")

    # Establish sufficient investment capital.
        post_investor_capital_event(
        investor_id=self.investor.pk,
        entry_type="contribution",
        amount=capital,
        effective_date=self.today,
        reference="CONTRIB-LOSS-001",
    )

        loss = Decimal("1000.00")
        allocation = self.create_allocation("loss", loss)

        allocation = approve_investor_allocation(
    allocation_id=allocation.pk,
    approved_by=self.admin_user,
)

        self.assert_journal_mapping(
        "loss",
        loss,
        "2000",
        "3100",
        "LOSS-001",
        allocation=allocation,
    )
        

    def test_contribution_journal_mapping(self):
        self.assert_journal_mapping(
        "contribution", Decimal("1000.00"),
        "1000", "2000", "CONTRIB-001",
    )


    def test_withdrawal_journal_mapping(self):
    # First establish sufficient investor capital.
        self.assert_journal_mapping(
        "contribution", Decimal("5000.00"),
        "1000", "2000", "CONTRIB-002",
    )

        self.assert_journal_mapping(
        "withdrawal", Decimal("1000.00"),
        "2000", "1000", "WITHDRAW-001",
    )

    def create_posted_journal(self, reference, amount):
        amount = Decimal(amount)

        journal = JournalEntry.objects.create(
            reference=f"JRN-{reference}",
            period=self.period,
            description="Capital test journal",
            status="draft",
        )

        JournalLine.objects.create(
            entry=journal,
            account=self.cash,
            debit=amount,
            credit=Decimal("0.00"),
            description="Test debit",
        )

        JournalLine.objects.create(
            entry=journal,
            account=self.capital,
            debit=Decimal("0.00"),
            credit=amount,
            description="Test credit",
        )

        return post_journal_entry(journal)

    def add_entry(self, entry_type, amount, reference):
        journal = self.create_posted_journal(reference, amount)

        return InvestorCapitalEntry.objects.create(
            investor=self.investor,
            entry_type=entry_type,
            amount=Decimal(amount),
            effective_date=self.today,
            reference=reference,
            journal_entry=journal,
            posted_at=timezone.now(),
        )

    def test_retained_profit_is_not_investment_capital(self):
        self.add_entry(
            "retained_profit",
            "100000.00",
            "RETAINED-001",
        )

        self.assertEqual(
            retained_profit_balance(self.investor),
            Decimal("100000.00"),
        )

        self.assertEqual(
            capital_balance(self.investor),
            Decimal("0.00"),
        )

    def test_reinvestment_moves_retained_profit_into_capital(self):
        self.add_entry(
            "retained_profit",
            "100000.00",
            "RETAINED-002",
        )

        journal = self.create_posted_journal(
            "REINVEST-001",
            "40000.00",
        )

        post_capital_entry(
            investor_id=self.investor.pk,
            entry_type="reinvestment",
            amount=Decimal("40000.00"),
            effective_date=self.today,
            reference="REINVEST-001",
            journal_entry=journal,
        )

        self.assertEqual(
            retained_profit_balance(self.investor),
            Decimal("60000.00"),
        )

        self.assertEqual(
            capital_balance(self.investor),
            Decimal("40000.00"),
        )

    def test_reinvestment_cannot_exceed_retained_profit(self):
        self.add_entry(
            "retained_profit",
            "30000.00",
            "RETAINED-003",
        )

        journal = self.create_posted_journal(
            "REINVEST-002",
            "40000.00",
        )

        with self.assertRaises(ValidationError):
            post_capital_entry(
                investor_id=self.investor.pk,
                entry_type="reinvestment",
                amount=Decimal("40000.00"),
                effective_date=self.today,
                reference="REINVEST-002",
                journal_entry=journal,
            )

        self.assertEqual(
            capital_balance(self.investor),
            Decimal("0.00"),
        )

    def test_withdrawal_cannot_exceed_investment_capital(self):
        self.add_entry(
            "contribution",
            "50000.00",
            "CONTRIBUTION-001",
        )

        journal = self.create_posted_journal(
            "WITHDRAWAL-001",
            "60000.00",
        )

        with self.assertRaises(ValidationError):
            post_capital_entry(
                investor_id=self.investor.pk,
                entry_type="withdrawal",
                amount=Decimal("60000.00"),
                effective_date=self.today,
                reference="WITHDRAWAL-001",
                journal_entry=journal,
            )

        self.assertEqual(
            capital_balance(self.investor),
            Decimal("50000.00"),
        )
        
        
    def test_wrong_journal_accounts_are_rejected(self):
        journal = self.create_posted_journal(
            "WRONG-ACCOUNTS",
            "25000.00",
        )

        with self.assertRaises(ValidationError):
            post_capital_entry(
                investor_id=self.investor.pk,
                entry_type="reinvestment",
                amount=Decimal("25000.00"),
                effective_date=self.today,
                reference="WRONG-ACCOUNTS-ENTRY",
                journal_entry=journal,
            )

        self.assertFalse(
            InvestorCapitalEntry.objects.filter(
                reference="WRONG-ACCOUNTS-ENTRY"
            ).exists()
        )

    def test_journal_amount_must_match_capital_entry(self):
        journal = self.create_posted_journal(
            "WRONG-AMOUNT",
            "20000.00",
        )

        with self.assertRaises(ValidationError):
            post_capital_entry(
                investor_id=self.investor.pk,
                entry_type="contribution",
                amount=Decimal("25000.00"),
                effective_date=self.today,
                reference="WRONG-AMOUNT-ENTRY",
                journal_entry=journal,
            )

    def test_journal_period_must_cover_effective_date(self):
        journal = self.create_posted_journal(
            "PERIOD-CHECK",
            "25000.00",
        )

        outside_date = self.period.end_date.replace(
            day=self.period.end_date.day
        )
        # Use the day after the journal period.
        from datetime import timedelta
        outside_date += timedelta(days=1)

        with self.assertRaises(ValidationError):
            post_capital_entry(
                investor_id=self.investor.pk,
                entry_type="contribution",
                amount=Decimal("25000.00"),
                effective_date=outside_date,
                reference="PERIOD-CHECK-ENTRY",
                journal_entry=journal,
            )
            
            
    def test_unapproved_allocation_cannot_be_posted(self):
        amount = Decimal("2000.00")
        allocation = self.create_allocation("profit", amount)

        journal = self.create_mapped_journal(
        "UNAPPROVED",
        amount,
        self.retained_earnings,
        self.retained_allocations,
    )

        with self.assertRaises(ValidationError):
            post_capital_entry(
            investor_id=self.investor.pk,
            entry_type="retained_profit",
            amount=amount,
            effective_date=self.today,
            reference="UNAPPROVED-ENTRY",
            allocation=allocation,
            journal_entry=journal,
        )

        self.assertFalse(
        InvestorCapitalEntry.objects.filter(
            reference="UNAPPROVED-ENTRY"
        ).exists()
    )


    def test_allocation_from_another_investor_is_rejected(self):
        other_user = User.objects.create_user(
        username="other_investor",
        password="test-password-123",
    )

        other_investor = Investor.objects.create(
        user=other_user,
        legal_name="Other Investor",
    )

        other_agreement = InvestorAgreement.objects.create(
        investor=other_investor,
        capital_basis="time_weighted_average",
        profit_treatment="retain",
        effective_from=self.today,
        profit_capital_component=Decimal("50.00"),
        profit_agreed_component=Decimal("50.00"),
        agreed_profit_share=Decimal("100.00"),
        loss_capital_component=Decimal("50.00"),
        loss_agreed_component=Decimal("50.00"),
        agreed_loss_share=Decimal("100.00"),
        approved=True,
    )

        amount = Decimal("2000.00")

        allocation = InvestorAllocation.objects.create(
        period=self.period,
        investor=other_investor,
        allocation_type="profit",
        amount=amount,
        capital_component=Decimal("1000.00"),
        agreed_component=Decimal("1000.00"),
        agreement=other_agreement,
    )

        allocation = approve_investor_allocation(
        allocation_id=allocation.pk,
        approved_by=self.admin_user,
    )

        journal = self.create_mapped_journal(
        "OTHER-INVESTOR",
        amount,
        self.retained_earnings,
        self.retained_allocations,
    )

        with self.assertRaises(ValidationError):
            post_capital_entry(
            investor_id=self.investor.pk,
            entry_type="retained_profit",
            amount=amount,
            effective_date=self.today,
            reference="OTHER-INVESTOR-ENTRY",
            allocation=allocation,
            journal_entry=journal,
        )


    def test_allocation_cannot_be_reused(self):
        amount = Decimal("2000.00")
        allocation = self.create_allocation("profit", amount)

        allocation = approve_investor_allocation(
        allocation_id=allocation.pk,
        approved_by=self.admin_user,
    )

        first_journal = self.create_mapped_journal(
        "ALLOCATION-FIRST",
        amount,
        self.retained_earnings,
        self.retained_allocations,
    )

        post_capital_entry(
        investor_id=self.investor.pk,
        entry_type="retained_profit",
        amount=amount,
        effective_date=self.today,
        reference="ALLOCATION-FIRST-ENTRY",
        allocation=allocation,
        journal_entry=first_journal,
    )

        second_journal = self.create_mapped_journal(
        "ALLOCATION-SECOND",
        amount,
        self.retained_earnings,
        self.retained_allocations,
    )

        with self.assertRaises(ValidationError):
            post_capital_entry(
            investor_id=self.investor.pk,
            entry_type="retained_profit",
            amount=amount,
            effective_date=self.today,
            reference="ALLOCATION-SECOND-ENTRY",
            allocation=allocation,
            journal_entry=second_journal,
        )


    def test_retained_profit_requires_allocation(self):
        amount = Decimal("2000.00")

        journal = self.create_mapped_journal(
        "MISSING-ALLOCATION",
        amount,
        self.retained_earnings,
        self.retained_allocations,
    )

        with self.assertRaises(ValidationError):
            post_capital_entry(
            investor_id=self.investor.pk,
            entry_type="retained_profit",
            amount=amount,
            effective_date=self.today,
            reference="MISSING-ALLOCATION-ENTRY",
            journal_entry=journal,
        )