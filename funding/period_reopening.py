from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from backoffice.audit import log as audit_log
from funding.models import FundingPeriod, FundingPeriodReopening
from funding.security import need


@transaction.atomic
def reopen_funding_period(*, period_id, reopened_by, reason):
    need(reopened_by, "funding.reopen_fundingperiod")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError("A reopening reason is required.")
    try:
        period = FundingPeriod.objects.select_for_update().get(pk=period_id)
    except FundingPeriod.DoesNotExist:
        raise ValidationError("Funding period not found.")
    if period.status != "closed":
        raise ValidationError("Only closed funding periods can be reopened.")
    if not period.closed_at:
        raise ValidationError("The period has no recorded closing timestamp.")

    FundingPeriodReopening.objects.create(period=period, reopened_by=reopened_by, reason=reason[:255],
                                          previous_closed_at=period.closed_at)
    period.status = "open"
    period.closed_at = None
    period.save(update_fields=["status", "closed_at"])
    audit_log(reopened_by, "period_reopened", f"period:{period.pk}", reason)
    return period

