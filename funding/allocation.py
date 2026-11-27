from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from backoffice.audit import log as audit_log

from .accounting import post_investor_capital_event
from .models import InvestorAllocation
from .security import need


@transaction.atomic
def approve_investor_allocation(*, allocation_id, approved_by):
    need(approved_by, "funding.approve_investorallocation")
    try:
        allocation = (InvestorAllocation.objects.select_for_update(of=("self",))
                      .select_related("period").get(pk=allocation_id))
    except InvestorAllocation.DoesNotExist:
        raise ValidationError("Allocation not found.")
    if allocation.approval_status != "pending":
        raise ValidationError("Only pending allocations can be approved.")
    if allocation.period.status != "review":
        raise ValidationError("The period must be under review to approve allocations.")
    if allocation.proposed_by_id is None:
        raise ValidationError("This allocation has no proposer on record, so it cannot be approved.")
    if allocation.proposed_by_id == approved_by.pk:
        raise PermissionDenied("The person who proposed an allocation cannot approve it.")

    allocation.approval_status = "approved"
    allocation.approved_by = approved_by
    allocation.approved_at = timezone.now()
    allocation.full_clean()
    allocation.save(update_fields=["approval_status", "approved_by", "approved_at"])
    audit_log(approved_by, "allocation_approved", f"allocation:{allocation.pk}", str(allocation.amount))
    return allocation


@transaction.atomic
def reject_investor_allocation(*, allocation_id, rejected_by):
    """Reject a pending allocation. It can then be replaced by proposing the period again."""
    need(rejected_by, "funding.approve_investorallocation")
    try:
        allocation = (InvestorAllocation.objects.select_for_update(of=("self",))
                      .select_related("period").get(pk=allocation_id))
    except InvestorAllocation.DoesNotExist:
        raise ValidationError("Allocation not found.")
    if allocation.approval_status != "pending":
        raise ValidationError("Only pending allocations can be rejected.")
    if allocation.period.status != "review":
        raise ValidationError("The period must be under review to reject allocations.")
    if allocation.proposed_by_id == rejected_by.pk:
        raise PermissionDenied("The person who proposed an allocation cannot reject it.")

    allocation.approval_status = "rejected"
    allocation.save(update_fields=["approval_status"])
    audit_log(rejected_by, "allocation_rejected", f"allocation:{allocation.pk}", str(allocation.amount))
    return allocation


@transaction.atomic
def post_allocation_to_capital(*, allocation_id, posted_by):
    """
    Turn an approved allocation into the investor's capital entries.

    A profit becomes retained profit, then is reinvested or paid out when the agreement
    says so (the payout entry records the obligation; the bank transfer is done by staff).
    A loss reduces the investor's capital.
    Returns the list of capital entries created.
    """
    need(posted_by, "funding.add_investorcapitalentry")
    try:
        allocation = (InvestorAllocation.objects.select_for_update(of=("self",))
                      .select_related("period", "agreement").get(pk=allocation_id))
    except InvestorAllocation.DoesNotExist:
        raise ValidationError("Allocation not found.")
    if allocation.approval_status != "approved":
        raise ValidationError("Only approved allocations can be posted.")
    if allocation.capital_entries.exists():
        raise ValidationError("This allocation has already been posted.")

    period = allocation.period
    label = f"{period} {allocation.get_allocation_type_display().lower()}"
    base = f"ALLOC-{allocation.pk}"
    kind = "retained_profit" if allocation.allocation_type == "profit" else "loss"

    entries = [post_investor_capital_event(
        posted_by=posted_by, investor_id=allocation.investor_id, entry_type=kind, amount=allocation.amount,
        effective_date=period.end_date, reference=base, description=f"Allocated {label}", allocation=allocation)]

    if kind == "retained_profit":
        follow_up = {"reinvest": "reinvestment", "payout": "payout"}.get(allocation.agreement.profit_treatment)
        if follow_up:
            entries.append(post_investor_capital_event(
                posted_by=posted_by, investor_id=allocation.investor_id, entry_type=follow_up,
                amount=allocation.amount, effective_date=period.end_date, reference=f"{base}-{follow_up.upper()}",
                description=f"{follow_up.capitalize()} of {label}", allow_review_period=True))
    return entries


def post_approved_allocation(*, allocation_id, posted_by):
    from .accounting import post_investor_capital_event
    try:
        a = InvestorAllocation.objects.select_related("period").get(pk=allocation_id)
    except InvestorAllocation.DoesNotExist:
        raise ValidationError("Allocation not found.")
    if a.approval_status != "approved":
        raise ValidationError("Approve the allocation first.")
    return post_investor_capital_event(
        posted_by=posted_by, investor_id=a.investor_id, allocation=a, amount=a.amount, effective_date=a.period.end_date,
        entry_type="retained_profit" if a.allocation_type == "profit" else "loss",
        reference=f"ALLOC-{a.pk}", description=f"{a.get_allocation_type_display()} for {a.period}")