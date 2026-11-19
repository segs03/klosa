from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from backoffice.audit import log as audit_log
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