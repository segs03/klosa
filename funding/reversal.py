from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction

from funding.models import JournalEntry, JournalLine
from funding.accounting import post_journal_entry, get_open_period


@transaction.atomic
def reverse_journal_entry(
    *,
    journal_id,
    reversed_by,
    reason,
    effective_date,
    reference,
):
    # Authorisation
    if (
        not reversed_by
        or not reversed_by.is_authenticated
        or not reversed_by.is_active
        or not reversed_by.is_staff
    ):
        raise PermissionDenied(
            "Only an active staff member can reverse journal entries."
        )

    reason = reason.strip()

    if not reason:
        raise ValidationError("A reversal reason is required.")

    # Lock the original journal.
    try:
        original = JournalEntry.objects.select_for_update().get(
            pk=journal_id
        )
    except JournalEntry.DoesNotExist:
        raise ValidationError("The original journal does not exist.")

    if original.status != "posted":
        raise ValidationError(
            "Only posted journal entries can be reversed."
        )

    if JournalEntry.objects.filter(reversal_of=original).exists():
        raise ValidationError(
            "This journal has already been reversed."
        )

    # The reversal must be recorded in an open period.
    period = get_open_period(effective_date)

    lines = list(
        original.lines.select_related("account").all()
    )

    if len(lines) < 2:
        raise ValidationError(
            "The original journal does not contain enough lines."
        )

    total_debit = sum(
        (line.debit for line in lines),
        Decimal("0.00"),
    )
    total_credit = sum(
        (line.credit for line in lines),
        Decimal("0.00"),
    )

    if total_debit <= 0 or total_debit != total_credit:
        raise ValidationError(
            "The original journal is not balanced."
        )

    if JournalEntry.objects.filter(reference=reference).exists():
        raise ValidationError(
            "This journal reference has already been used."
        )

    # Create the reversing journal.
    reversal = JournalEntry.objects.create(
        reference=reference,
        period=period,
        description=f"Reversal of {original.reference}: {reason}",
        reversal_of=original,
        reversal_reason=reason,
        reversed_by=reversed_by,
    )

    # Reverse every journal line.
    for line in lines:
        JournalLine.objects.create(
            entry=reversal,
            account=line.account,
            debit=line.credit,
            credit=line.debit,
            description=f"Reversal: {line.description}",
        )

    # Post the reversal.
    reversal = post_journal_entry(reversal)

    # Mark the original journal as reversed.
    original.status = "reversed"
    original.save(update_fields=["status"])

    return reversal