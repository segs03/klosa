

from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (JournalEntry, JournalLine, FundingPeriod,FundingPeriod, Investor, InvestorCapitalEntry, LedgerAccount, InvestorAllocation,
)


# Keep these codes aligned with the chart of accounts.
ACCOUNT_CODES = {
    "cash": "1000",
    "investor_capital": "2000",
    "company_capital": "2100",
    "retained_allocations": "2200",
    "retained_earnings": "3000",
    "loss_clearing": "3100",
}

CAPITAL_INCREASE = {"contribution", "reinvestment"}
CAPITAL_DECREASE = {"withdrawal", "loss"}
RETAINED_INCREASE = {"retained_profit"}
RETAINED_DECREASE = {"payout", "reinvestment"}


def get_ledger_account(code):
    try:
        return LedgerAccount.objects.get(
            code=ACCOUNT_CODES[code],
            is_active=True,
        )
    except LedgerAccount.DoesNotExist:
        raise ValidationError(
            f"Required ledger account is missing: {code}"
        )


def add_journal_line(entry, account_key, debit, credit, description):
    JournalLine.objects.create(
        entry=entry,
        account=get_ledger_account(account_key),
        debit=debit,
        credit=credit,
        description=description,
    )


def get_open_period(effective_date):
    try:
        return FundingPeriod.objects.get(
            start_date__lte=effective_date,
            end_date__gte=effective_date,
            status="open",
        )
    except FundingPeriod.DoesNotExist:
        raise ValidationError(
            "No open accounting period covers this effective date."
        )


@transaction.atomic
def post_investor_capital_event(
    *,
    investor_id,
    entry_type,
    amount,
    effective_date,
    reference,
    description="",
    allocation=None,
):
    amount = Decimal(str(amount)).quantize(Decimal("0.01"))

    if amount <= Decimal("0.00"):
        raise ValidationError("Amount must be greater than zero.")

    valid_types = (
        CAPITAL_INCREASE
        | CAPITAL_DECREASE
        | RETAINED_INCREASE
        | RETAINED_DECREASE
    )

    if entry_type not in valid_types:
        raise ValidationError("Invalid capital entry type.")

    # Lock the investor while checking balances and creating the entry.
    investor = Investor.objects.select_for_update().get(
        pk=investor_id
    )

    if InvestorCapitalEntry.objects.filter(
        reference=reference
    ).exists():
        raise ValidationError("This reference has already been used.")

    period = get_open_period(effective_date)

    # Profit and loss events must have an approved allocation.
    if entry_type in {"retained_profit", "loss"} and allocation is None:
        raise ValidationError(
        "This capital event requires an allocation."
    )

    if allocation is not None:
    # Lock and refresh the allocation record.
        try:
            allocation = InvestorAllocation.objects.select_for_update().get(
            pk=allocation.pk
        )
        except InvestorAllocation.DoesNotExist:
            raise ValidationError("The allocation does not exist.")

        if allocation.investor_id != investor.pk:
            raise ValidationError(
            "The allocation does not belong to this investor."
        )

        if allocation.period_id != period.pk:
            raise ValidationError(
            "The allocation and journal period do not match."
        )

        if entry_type == "retained_profit":
            if allocation.allocation_type != "profit":
                raise ValidationError(
                "Retained profit requires a profit allocation."
            )

        elif entry_type == "loss":
            if allocation.allocation_type != "loss":
                raise ValidationError(
                "A loss entry requires a loss allocation."
            )
            
        if entry_type in {"retained_profit", "loss"}:
            if allocation.approval_status != "approved":
                raise ValidationError(
            "The allocation must be approved before posting."
        )

        if entry_type in {"retained_profit", "loss"}:
            if allocation.amount != amount:
                raise ValidationError(
                "The event amount must match the allocation amount."
            )

            if InvestorCapitalEntry.objects.filter(
            allocation=allocation
        ).exists():
                raise ValidationError(
                "This allocation has already been used."
            )

    # Import locally to avoid circular imports.
    from .capital import (
        capital_balance,
        retained_profit_balance,
    )

    if entry_type in CAPITAL_DECREASE:
        if capital_balance(investor) < amount:
            raise ValidationError(
                "Insufficient investment capital."
            )

    if entry_type in RETAINED_DECREASE:
        if retained_profit_balance(investor) < amount:
            raise ValidationError(
                "Insufficient retained profit."
            )

    # Create the journal and capital record in one database transaction.
    journal = JournalEntry.objects.create(
        reference=f"JRN-{reference}",
        period=period,
        description=description or entry_type,
        status="draft",
    )

    cash = "cash"
    capital = "investor_capital"
    retained = "retained_allocations"
    earnings = "retained_earnings"
    loss_clearing = "loss_clearing"

    if entry_type == "contribution":
        # Dr Cash / Cr Investor capital
        add_journal_line(
            journal, cash, amount, 0, description
        )
        add_journal_line(
            journal, capital, 0, amount, description
        )

    elif entry_type == "withdrawal":
        # Dr Investor capital / Cr Cash
        add_journal_line(
            journal, capital, amount, 0, description
        )
        add_journal_line(
            journal, cash, 0, amount, description
        )

    elif entry_type == "retained_profit":

        # Dr Retained earnings / Cr Investor retained allocations
        add_journal_line(
            journal, earnings, amount, 0, description
        )
        add_journal_line(
            journal, retained, 0, amount, description
        )

    elif entry_type == "reinvestment":
        # Dr Investor retained allocations / Cr Investor capital
        add_journal_line(
            journal, retained, amount, 0, description
        )
        add_journal_line(
            journal, capital, 0, amount, description
        )

    elif entry_type == "payout":
        # Dr Investor retained allocations / Cr Cash
        add_journal_line(
            journal, retained, amount, 0, description
        )
        add_journal_line(
            journal, cash, 0, amount, description
        )

    elif entry_type == "loss":

        # Dr Investor capital / Cr Loss allocation clearing
        add_journal_line(
            journal, capital, amount, 0, description
        )
        add_journal_line(
            journal, loss_clearing, 0, amount, description
        )

    # Post the journal before recording the capital entry.
    # Both operations are protected by this outer transaction.
    journal = post_journal_entry(journal)

    capital_entry = InvestorCapitalEntry.objects.create(
        investor=investor,
        entry_type=entry_type,
        amount=amount,
        effective_date=effective_date,
        reference=reference,
        description=description,
        allocation=allocation,
        journal_entry=journal,
        posted_at=timezone.now(),
    )

    return capital_entry



CENT = Decimal("0.01")


@transaction.atomic
def post_journal_entry(entry):
    entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)

    if entry.status != "draft":
        raise ValidationError("Only draft entries can be posted.")

    if entry.period.status != "open":
        raise ValidationError(
        "Journal entries can only be posted in an open period."
    )

    lines = list(entry.lines.all())

    if len(lines) < 2:
        raise ValidationError("A journal entry needs at least two lines.")

    total_debit = sum((line.debit for line in lines), Decimal("0.00"))
    total_credit = sum((line.credit for line in lines), Decimal("0.00"))

    if total_debit <= 0 or total_debit != total_credit:
        raise ValidationError("Journal entry is not balanced.")

    entry.status = "posted"
    entry.posted_at = timezone.now()
    entry.save(update_fields=["status", "posted_at"])

    return entry
