from .capital import time_weighted_average_capital, capital_balance_before

from django.db import models
from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import (
    InvestorAgreement,
    InvestorAllocation,
    CapitalContribution,
    FundingPeriod,
)


CENT = Decimal("0.01")
ZERO = Decimal("0.00")
HUNDRED = Decimal("100.00")


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def percentage(value):
    return Decimal(value) / HUNDRED


def split_amount(total, weights):
    """
    weights: list of (object, percentage)
    Returns amounts whose sum equals total, including rounding remainder.
    """
    results = []
    allocated = ZERO

    for index, (obj, weight) in enumerate(weights):
        if index == len(weights) - 1:
            amount = money(total - allocated)
        else:
            amount = money(total * percentage(weight))
            allocated += amount

        results.append((obj, amount))

    return results


@transaction.atomic
def allocate_monthly_result(period_id, approved_result):
    """
    approved_result:
        Positive = profit to allocate.
        Negative = loss to allocate.

    Call only after the monthly accounts have been reconciled
    and the result has been approved.
    """
    period = FundingPeriod.objects.select_for_update().get(pk=period_id)

    if period.status != "review":
        raise ValidationError(
            "The period must be under review before allocation."
        )

    result = money(approved_result)

    if result == ZERO:
        return []

    allocation_type = "profit" if result > ZERO else "loss"
    distributable = abs(result)

    agreements = list(
        InvestorAgreement.objects.filter(
            approved=True,
            effective_from__lte=period.end_date,
        ).filter(
            models.Q(effective_to__isnull=True)
            | models.Q(effective_to__gte=period.start_date)
        ).select_related("investor")
    )

    if not agreements:
        raise ValidationError("No approved investor agreements apply.")

    # Ensure each investor has only one applicable agreement.
    investor_ids = [a.investor_id for a in agreements]
    if len(investor_ids) != len(set(investor_ids)):
        raise ValidationError(
            "Overlapping investor agreements exist for this period."
        )

    if allocation_type == "profit":
        capital_component_field = "profit_capital_component"
        agreed_component_field = "profit_agreed_component"
        agreed_share_field = "agreed_profit_share"
    else:
        capital_component_field = "loss_capital_component"
        agreed_component_field = "loss_agreed_component"
        agreed_share_field = "agreed_loss_share"

    capital_components = {
        getattr(a, capital_component_field) for a in agreements
    }

    if len(capital_components) != 1:
        raise ValidationError(
            "All applicable agreements must use the same capital component."
        )

    capital_component_pct = capital_components.pop()
    agreed_component_pct = HUNDRED - capital_component_pct

    agreed_shares_total = sum(
        (getattr(a, agreed_share_field) for a in agreements), ZERO
    )

    if agreed_shares_total != HUNDRED:
        raise ValidationError(
            "Agreed investor shares must total exactly 100%."
        )

    # Sum verified investor contributions received by period end.
    # A production implementation should also account for withdrawals,
    # capital impairment and the agreement's precise eligible-capital rules.
    capital_by_investor = {}

    for agreement in agreements:
    investor = agreement.investor

        if agreement.capital_basis == "opening_balance":
            eligible_capital = capital_balance_before(
            investor, period.start_date
        )
        else:
            eligible_capital = time_weighted_average_capital(
            investor, period
        )

        if eligible_capital < ZERO:
            raise ValidationError("Investor capital cannot be negative.")

        capital_by_investor[investor.pk] = eligible_capital

    total_eligible_capital = sum(
    capital_by_investor.values(), ZERO
)

    total_eligible_capital = sum(capital_by_investor.values(), ZERO)

    if capital_component_pct > ZERO and total_eligible_capital <= ZERO:
        raise ValidationError(
            "No eligible investor capital is available for allocation."
        )

    capital_pool = money(
        distributable * percentage(capital_component_pct)
    )
    agreed_pool = distributable - capital_pool

    capital_weights = [
        (
            a,
            (
                capital_by_investor[a.investor_id]
                / total_eligible_capital
                * HUNDRED
                if total_eligible_capital else ZERO
            ),
        )
        for a in agreements
    ]

    agreed_weights = [
        (a, getattr(a, agreed_share_field))
        for a in agreements
    ]

    capital_allocations = dict(
        (a.pk, amount)
        for a, amount in split_amount(capital_pool, capital_weights)
    )
    agreed_allocations = dict(
        (a.pk, amount)
        for a, amount in split_amount(agreed_pool, agreed_weights)
    )

    created = []

    for agreement in agreements:
        capital_amount = capital_allocations[agreement.pk]
        agreed_amount = agreed_allocations[agreement.pk]

        created.append(
            InvestorAllocation.objects.create(
                period=period,
                investor=agreement.investor,
                allocation_type=allocation_type,
                capital_component=capital_amount,
                agreed_component=agreed_amount,
                amount=capital_amount + agreed_amount,
                agreement=agreement,
            )
        )

    return created
