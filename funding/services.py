from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import models, transaction

from backoffice.audit import log as audit_log

from .capital import capital_balance_before, time_weighted_average_capital
from .models import FundingPeriod, InvestorAgreement, InvestorAllocation
from .security import need
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
HUNDRED = Decimal("100.00")



def money(value):
    try:
        amount = Decimal(str(value).strip())
        if not amount.is_finite():
            raise InvalidOperation
        return amount.quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError("Enter a valid amount.")


def percentage(value):
    return Decimal(value) / HUNDRED


def split_amount(total, weights):
    """
    weights: list of (object, percentage)
    Returns (object, amount) pairs whose amounts add up to total exactly.
    The last item takes the rounding remainder.
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
def allocate_monthly_result(period_id, approved_result, *, proposed_by):
    """
    Propose the investor allocations for a period.

    approved_result: positive = profit to allocate, negative = loss to allocate.

    Call only after the monthly accounts are reconciled and the result is approved.
    The allocations are created as PENDING with `proposed_by` on record; a different
    person must approve each one (see funding.allocation.approve_investor_allocation).
    Running this again replaces allocations that are still pending or rejected, but
    never touches an approved allocation.
    """
    need(proposed_by, "funding.propose_investorallocation")

    try:
        period = FundingPeriod.objects.select_for_update().get(pk=period_id)
    except FundingPeriod.DoesNotExist:
        raise ValidationError("Funding period not found.")

    if period.status != "review":
        raise ValidationError("The period must be under review before allocation.")

    result = money(approved_result)
    if result == ZERO:
        return []

    allocation_type = "profit" if result > ZERO else "loss"
    distributable = abs(result)

    existing = InvestorAllocation.objects.filter(period=period)
    if existing.filter(approval_status="approved").exists():
        raise ValidationError("Approved allocations already exist for this period.")
    if existing.exclude(allocation_type=allocation_type).exists():
        raise ValidationError(
            "This period already has allocations of the other type. Delete or reject them first."
        )

    agreements = list(
        InvestorAgreement.objects.filter(
            approved=True,
            investor__is_active=True,
            effective_from__lte=period.end_date,
        )
        .filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=period.start_date))
        .select_related("investor")
        .order_by("investor_id", "pk")
    )

    if not agreements:
        raise ValidationError("No approved investor agreements apply.")

    investor_ids = [a.investor_id for a in agreements]
    if len(investor_ids) != len(set(investor_ids)):
        raise ValidationError("Overlapping investor agreements exist for this period.")

    if allocation_type == "profit":
        capital_component_field = "profit_capital_component"
        agreed_share_field = "agreed_profit_share"
    else:
        capital_component_field = "loss_capital_component"
        agreed_share_field = "agreed_loss_share"

    capital_components = {getattr(a, capital_component_field) for a in agreements}
    if len(capital_components) != 1:
        raise ValidationError("All applicable agreements must use the same capital component.")
    capital_component_pct = capital_components.pop()

    agreed_shares_total = sum((getattr(a, agreed_share_field) for a in agreements), ZERO)
    if capital_component_pct < HUNDRED and agreed_shares_total != HUNDRED:
        raise ValidationError("Agreed investor shares must total exactly 100%.")

    # Eligible capital per investor, by the basis in their agreement.
    capital_by_investor = {}
    for agreement in agreements:
        investor = agreement.investor
        if agreement.capital_basis == "opening_balance":
            eligible = capital_balance_before(investor, period.start_date)
        else:
            eligible = time_weighted_average_capital(investor, period)
        if eligible < ZERO:
            raise ValidationError("Investor capital cannot be negative.")
        capital_by_investor[investor.pk] = eligible

    total_eligible = sum(capital_by_investor.values(), ZERO)
    if capital_component_pct > ZERO and total_eligible <= ZERO:
        raise ValidationError("No eligible investor capital is available for allocation.")

    capital_pool = money(distributable * percentage(capital_component_pct))
    agreed_pool = distributable - capital_pool

    capital_weights = [
        (a, (capital_by_investor[a.investor_id] / total_eligible * HUNDRED) if total_eligible else ZERO)
        for a in agreements
    ]
    agreed_weights = [(a, getattr(a, agreed_share_field)) for a in agreements]

    capital_amounts = {a.pk: amt for a, amt in split_amount(capital_pool, capital_weights)}
    agreed_amounts = {a.pk: amt for a, amt in split_amount(agreed_pool, agreed_weights)}

    # Replace earlier proposals that were never approved (pending or rejected).
    for stale in existing:
        stale.delete()

    created = []
    for agreement in agreements:
        capital_amount = capital_amounts[agreement.pk]
        agreed_amount = agreed_amounts[agreement.pk]
        amount = capital_amount + agreed_amount
        if amount <= ZERO:
            continue  # an investor with no share this period gets no row
        created.append(
            InvestorAllocation.objects.create(
                period=period,
                investor=agreement.investor,
                allocation_type=allocation_type,
                capital_component=capital_amount,
                agreed_component=agreed_amount,
                amount=amount,
                agreement=agreement,
                proposed_by=proposed_by,
            )
        )

    audit_log(proposed_by, "allocations_proposed", f"period:{period.pk}", str(result))
    return created
