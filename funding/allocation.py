from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from .models import InvestorAllocation


@transaction.atomic
def approve_investor_allocation(*, allocation_id, approved_by):

    if (
    not approved_by
    or not approved_by.is_authenticated
    or not approved_by.is_active
    or not approved_by.is_staff
    or not approved_by.has_perm(
        "funding.approve_investorallocation"
    )
):
        raise PermissionDenied(
        "You are not authorised to approve investor allocations."
    )

    allocation = InvestorAllocation.objects.select_for_update().get(
        pk=allocation_id
    )

    if allocation.approval_status != "pending":
        raise ValidationError(
            "Only pending allocations can be approved."
        )

    allocation.approval_status = "approved"
    allocation.approved_by = approved_by
    allocation.approved_at = timezone.now()

    allocation.full_clean()
    allocation.save(
        update_fields=[
            "approval_status",
            "approved_by",
            "approved_at",
        ]
    )

    return allocation