from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from funding.models import FundingPeriod, FundingPeriodReopening


@transaction.atomic
def reopen_funding_period(*, period_id, reopened_by, reason):
    if (
        not reopened_by
        or not reopened_by.is_authenticated
        or not reopened_by.is_active
        or not reopened_by.is_staff
        or not reopened_by.has_perm("funding.reopen_fundingperiod")
    ):
        raise PermissionDenied(
            "You do not have permission to reopen funding periods."
        )

    reason = reason.strip()

    if not reason:
        raise ValidationError("A reopening reason is required.")

    period = FundingPeriod.objects.select_for_update().get(
        pk=period_id
    )

    if period.status != "closed":
        raise ValidationError(
            "Only closed funding periods can be reopened."
        )

    if not period.closed_at:
        raise ValidationError(
            "The period has no recorded closing timestamp."
        )

    FundingPeriodReopening.objects.create(
        period=period,
        reopened_by=reopened_by,
        reason=reason,
        previous_closed_at=period.closed_at,
    )

    period.status = "open"
    period.closed_at = None
    period.save(update_fields=["status", "closed_at"])

    return period