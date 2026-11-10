from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from funding.models import (
    FundingPeriod,
    JournalEntry,
    InvestorAllocation,
)


def _check_period_permission(user, permission):
    if (
        not user
        or not user.is_authenticated
        or not user.is_active
        or not user.is_staff
        or not user.has_perm(permission)
    ):
        raise PermissionDenied(
            "You are not authorised to perform this operation."
        )


@transaction.atomic
def start_period_review(*, period_id, performed_by):
    _check_period_permission(
        performed_by,
        "funding.review_fundingperiod",
    )

    period = FundingPeriod.objects.select_for_update().get(
        pk=period_id
    )

    if period.status != "open":
        raise ValidationError(
            "Only open periods can enter review."
        )

    period.status = "review"
    period.save(update_fields=["status"])

    return period


@transaction.atomic
def close_funding_period(*, period_id, performed_by):
    _check_period_permission(
        performed_by,
        "funding.close_fundingperiod",
    )

    period = FundingPeriod.objects.select_for_update().get(
        pk=period_id
    )

    if period.status != "review":
        raise ValidationError(
            "Only periods under review can be closed."
        )

    if JournalEntry.objects.filter(
        period=period,
        status="draft",
    ).exists():
        raise ValidationError(
            "Cannot close a period containing draft journals."
        )

    journals = JournalEntry.objects.filter(
        period=period,
        status="posted",
    ).prefetch_related("lines")

    for journal in journals:
        lines = list(journal.lines.all())

        debit_total = sum(
            (line.debit for line in lines),
            start=0,
        )
        credit_total = sum(
            (line.credit for line in lines),
            start=0,
        )

        if len(lines) < 2 or debit_total <= 0:
            raise ValidationError(
                f"Journal {journal.reference} has invalid lines."
            )

        if debit_total != credit_total:
            raise ValidationError(
                f"Journal {journal.reference} is not balanced."
            )

    if InvestorAllocation.objects.filter(
        period=period,
        approval_status="pending",
    ).exists():
        raise ValidationError(
            "Cannot close a period with pending investor allocations."
        )

    period.status = "closed"
    period.closed_at = timezone.now()
    period.save(update_fields=["status", "closed_at"])

    return period