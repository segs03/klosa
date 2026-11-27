from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from backoffice.audit import log as audit_log
from funding.security import need

from .models import (
    FundingPeriod,
    Investor,
    InvestorAllocation,
    InvestorCapitalEntry,
    JournalEntry,
    JournalLine,
    LedgerAccount,
)

ZERO = Decimal("0.00")

# Keep these codes aligned with seed_ledger's chart of accounts.
ACCOUNT_CODES = {
    "cash": "1000",
    "investor_capital": "2000",
    "company_capital": "2100",
    "retained_allocations": "2200",
    "customer_deposits": "2300",
    "retained_earnings": "3000",
    "loss_clearing": "3100",
    "bonus_expense": "5200",
    "interest_expense": "5300",
}

CAPITAL_INCREASE = {"contribution", "reinvestment"}
CAPITAL_DECREASE = {"withdrawal", "loss"}
RETAINED_INCREASE = {"retained_profit"}
RETAINED_DECREASE = {"payout", "reinvestment"}

# Profit and loss entries come from approved allocations, and allocations are approved while
# the period is under review, so those entries may be posted in an open OR a review period.
ALLOCATION_ENTRY_TYPES = {"retained_profit", "loss"}


def get_ledger_account(code):
    try:
        return LedgerAccount.objects.get(code=ACCOUNT_CODES[code], is_active=True)
    except LedgerAccount.DoesNotExist:
        raise ValidationError(f"Required ledger account is missing: {code}")


def add_journal_line(entry, account_key, debit, credit, description):
    return JournalLine.objects.create(
        entry=entry,
        account=get_ledger_account(account_key),
        debit=Decimal(str(debit)),
        credit=Decimal(str(credit)),
        description=(description or "")[:255],
    )


def get_open_period(effective_date, statuses=("open",)):
    """The single period covering the date, locked, in one of the allowed statuses."""
    periods = list(
        FundingPeriod.objects.select_for_update().filter(
            start_date__lte=effective_date,
            end_date__gte=effective_date,
            status__in=statuses,
        )
    )
    if len(periods) != 1:
        if tuple(statuses) == ("open",):
            raise ValidationError("Exactly one open accounting period must cover this date.")
        raise ValidationError("Exactly one open or review accounting period must cover this date.")
    return periods[0]


def _clean_amount(value):
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValidationError("Enter a valid amount.")
    if not amount.is_finite() or amount <= ZERO:
        raise ValidationError("Amount must be greater than zero.")
    return amount


def _validate_allocation(allocation, investor, period, entry_type, amount):
    try:
        allocation = InvestorAllocation.objects.select_for_update().get(pk=allocation.pk)
    except InvestorAllocation.DoesNotExist:
        raise ValidationError("The allocation does not exist.")

    if allocation.investor_id != investor.pk:
        raise ValidationError("The allocation does not belong to this investor.")
    if allocation.period_id != period.pk:
        raise ValidationError("The allocation and journal period do not match.")

    if entry_type in ALLOCATION_ENTRY_TYPES:
        expected = "profit" if entry_type == "retained_profit" else "loss"
        if allocation.allocation_type != expected:
            raise ValidationError(f"A {entry_type.replace('_', ' ')} entry requires a {expected} allocation.")
        if allocation.approval_status != "approved":
            raise ValidationError("The allocation must be approved before posting.")
        if allocation.amount != amount:
            raise ValidationError("The event amount must match the allocation amount.")
        if InvestorCapitalEntry.objects.filter(allocation=allocation).exists():
            raise ValidationError("This allocation has already been used.")
    return allocation


@transaction.atomic
def post_investor_capital_event(
    *,
    posted_by,
    investor_id,
    entry_type,
    amount,
    effective_date,
    reference,
    description="",
    allocation=None,
    allow_review_period=False,
):
    need(posted_by, "funding.add_investorcapitalentry")
    amount = _clean_amount(amount)

    valid_types = CAPITAL_INCREASE | CAPITAL_DECREASE | RETAINED_INCREASE | RETAINED_DECREASE
    if entry_type not in valid_types:
        raise ValidationError("Invalid capital entry type.")

    # Lock the investor while checking balances and creating the entry.
    try:
        investor = Investor.objects.select_for_update().get(pk=investor_id)
    except Investor.DoesNotExist:
        raise ValidationError("Investor not found.")
    if not investor.is_active:
        raise ValidationError("This investor is not active.")

    if InvestorCapitalEntry.objects.filter(reference=reference).exists():
        raise ValidationError("This reference has already been used.")

    # Allocation entries, and the reinvestment/payout that follow them, may post while the period is in review.
    allow_review = allow_review_period or entry_type in ALLOCATION_ENTRY_TYPES
    period = get_open_period(
        effective_date, ("open", "review") if allow_review else ("open",))

    if entry_type in ALLOCATION_ENTRY_TYPES and allocation is None:
        raise ValidationError("This capital event requires an allocation.")
    if allocation is not None:
        allocation = _validate_allocation(allocation, investor, period, entry_type, amount)

    from .capital import capital_balance, retained_profit_balance  # avoid a circular import

    if entry_type in CAPITAL_DECREASE and capital_balance(investor) < amount:
        raise ValidationError("Insufficient investment capital.")
    if entry_type in RETAINED_DECREASE and retained_profit_balance(investor) < amount:
        raise ValidationError("Insufficient retained profit.")

    journal = JournalEntry.objects.create(
        reference=f"JRN-{reference}",
        period=period,
        description=(description or entry_type)[:255],
        status="draft",
    )

    # (debit account, credit account) for every entry type.
    mapping = {
        "contribution": ("cash", "investor_capital"),
        "withdrawal": ("investor_capital", "cash"),
        "retained_profit": ("retained_earnings", "retained_allocations"),
        "reinvestment": ("retained_allocations", "investor_capital"),
        "payout": ("retained_allocations", "cash"),
        "loss": ("investor_capital", "loss_clearing"),
    }
    debit_key, credit_key = mapping[entry_type]
    add_journal_line(journal, debit_key, amount, 0, description)
    add_journal_line(journal, credit_key, 0, amount, description)

    journal = post_journal_entry(journal, allow_review=allow_review)

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

    audit_log(posted_by, "capital_" + entry_type, reference, str(amount))
    return capital_entry


@transaction.atomic
def post_journal_entry(entry, allow_review=False):
    entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)

    if entry.status != "draft":
        raise ValidationError("Only draft entries can be posted.")

    allowed = ("open", "review") if allow_review else ("open",)
    if entry.period.status not in allowed:
        raise ValidationError("Journal entries can only be posted in an open period.")

    lines = list(entry.lines.all())
    if len(lines) < 2:
        raise ValidationError("A journal entry needs at least two lines.")

    total_debit = sum((line.debit for line in lines), ZERO)
    total_credit = sum((line.credit for line in lines), ZERO)
    if total_debit <= 0 or total_debit != total_credit:
        raise ValidationError("Journal entry is not balanced.")

    entry.status = "posted"
    entry.posted_at = timezone.now()
    entry.save(update_fields=["status", "posted_at"])
    return entry
