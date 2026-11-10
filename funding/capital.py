
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Investor, InvestorCapitalEntry, FundingPeriod, JournalEntry

from funding.allocation import InvestorAllocation



ZERO = Decimal("0.00")

CAPITAL_INCREASE_TYPES = {"contribution", "reinvestment"}
CAPITAL_DECREASE_TYPES = {"withdrawal", "loss"}
RETAINED_INCREASE_TYPES = {"retained_profit"}
RETAINED_DECREASE_TYPES = {"payout", "reinvestment"}

def _balance(investor, entry_types, before=None):
    entries = InvestorCapitalEntry.objects.filter(
        investor=investor,
        entry_type__in=entry_types,
        posted_at__isnull=False,
    )

    if before is not None:
        entries = entries.filter(effective_date__lt=before)

    total = entries.aggregate(total=Sum("amount"))["total"]
    return total or ZERO


def capital_balance(investor):
    increased = _balance(investor, CAPITAL_INCREASE_TYPES)
    decreased = _balance(investor, CAPITAL_DECREASE_TYPES)
    return increased - decreased


def retained_profit_balance(investor):
    increased = _balance(investor, RETAINED_INCREASE_TYPES)
    decreased = _balance(investor, RETAINED_DECREASE_TYPES)
    return increased - decreased


@transaction.atomic
def post_capital_entry(
    *,
    investor_id,
    entry_type,
    amount,
    effective_date,
    reference,
    description="",
    allocation=None,
    journal_entry,
):
    amount = Decimal(str(amount)).quantize(Decimal("0.01"))

    if amount <= ZERO:
        raise ValidationError("Amount must be greater than zero.")

    valid_types = (
        CAPITAL_INCREASE_TYPES
        | CAPITAL_DECREASE_TYPES
        | RETAINED_INCREASE_TYPES
        | RETAINED_DECREASE_TYPES
    )

    if entry_type not in valid_types:
        raise ValidationError("Invalid capital entry type.")

    investor = Investor.objects.select_for_update().get(
        pk=investor_id
    )

    if InvestorCapitalEntry.objects.filter(reference=reference).exists():
        raise ValidationError("This reference has already been used.")

    if entry_type in CAPITAL_DECREASE_TYPES:
        if capital_balance(investor) < amount:
            raise ValidationError(
                "Insufficient investment capital for this entry."
            )

    if entry_type in RETAINED_DECREASE_TYPES:
        if retained_profit_balance(investor) < amount:
            raise ValidationError(
                "Insufficient retained profit for this payout."
            )

    if entry_type == "reinvestment":
        if retained_profit_balance(investor) < amount:
            raise ValidationError(
                "Insufficient retained profit to reinvest."
            )

    if journal_entry and journal_entry.status != "posted":
        raise ValidationError(
            "The linked journal entry must already be posted."
        )
        
    # Fetch the latest journal record
    journal = JournalEntry.objects.select_for_update().get(
    pk=journal_entry.pk
)

    if journal.period.status != "open":
        raise ValidationError(
        "Capital entries can only be posted in an open period."
    )

    if journal.status != "posted":
        raise ValidationError("The journal entry must be posted.")

# Confirm the effective date falls inside the journal's period
    if not (
    journal.period.start_date
    <= effective_date
    <= journal.period.end_date
):
        raise ValidationError(
        "The journal period does not cover the capital entry date."
    )
    
    
        # Validate allocation when supplied
    if allocation is not None:
        allocation = InvestorAllocation.objects.select_for_update().get(
            pk=allocation.pk
        )

        if allocation.approval_status != "approved":
            raise ValidationError(
                "The allocation must be approved before posting."
            )

        if allocation.investor_id != investor.pk:
            raise ValidationError(
                "The allocation does not belong to this investor."
            )

        if allocation.period_id != journal.period_id:
            raise ValidationError(
                "The allocation and journal periods must match."
            )

        expected_type = {
            "retained_profit": "profit",
            "loss": "loss",
        }.get(entry_type)

        if expected_type and allocation.allocation_type != expected_type:
            raise ValidationError(
                "The allocation type does not match the capital entry."
            )

        if expected_type and allocation.amount != amount:
            raise ValidationError(
                "The capital entry amount must match the allocation."
            )

        if InvestorCapitalEntry.objects.filter(
            allocation=allocation
        ).exists():
            raise ValidationError(
                "This allocation has already been used."
            )

    elif entry_type in {"retained_profit", "loss"}:
        raise ValidationError(
            "This capital entry requires an allocation."
        )

# Confirm the journal totals match the capital entry amount
    lines = list(
    journal.lines.select_related("account").all()
)

    total_debit = sum(
    (line.debit for line in lines), ZERO
)
    total_credit = sum(
    (line.credit for line in lines), ZERO
)

    if total_debit != amount or total_credit != amount:
        raise ValidationError(
        "Journal totals must match the capital entry amount."
    )

    return InvestorCapitalEntry.objects.create(
        investor=investor,
        entry_type=entry_type,
        amount=amount,
        effective_date=effective_date,
        reference=reference,
        description=description,
        allocation=allocation,
        journal_entry=journal_entry,
        posted_at=timezone.now(),
    )


def time_weighted_average_capital(investor, period):
    """
    Daily time-weighted average capital.

    Entries effective on a date affect capital from that date.
    Only posted entries are included.
    """
    days = (period.end_date - period.start_date).days + 1

    if days <= 0:
        raise ValidationError("Invalid accounting period.")

    opening = capital_balance_before(investor, period.start_date)

    entries = InvestorCapitalEntry.objects.filter(
        investor=investor,
        posted_at__isnull=False,
        effective_date__gte=period.start_date,
        effective_date__lte=period.end_date,
        entry_type__in=(
            CAPITAL_INCREASE_TYPES | CAPITAL_DECREASE_TYPES
        ),
    ).order_by("effective_date", "created_at", "pk")

    balance = opening
    weighted_total = ZERO
    current_date = period.start_date

    for entry in entries:
        holding_days = (entry.effective_date - current_date).days
        weighted_total += balance * holding_days

        if entry.entry_type in CAPITAL_INCREASE_TYPES:
            balance += entry.amount
        else:
            balance -= entry.amount

        if balance < ZERO:
            raise ValidationError(
                "Capital ledger produces a negative balance."
            )

        current_date = entry.effective_date

    remaining_days = (period.end_date - current_date).days + 1
    weighted_total += balance * remaining_days

    return (weighted_total / Decimal(days)).quantize(
        Decimal("0.01")
    )


def capital_balance_before(investor, start_date):
    increased = _balance(
        investor, CAPITAL_INCREASE_TYPES, before=start_date
    )
    decreased = _balance(
        investor, CAPITAL_DECREASE_TYPES, before=start_date
    )
    return increased - decreased
