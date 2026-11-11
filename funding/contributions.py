
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    CapitalContribution,
    FundingPeriod,
    InvestorCapitalEntry,
    JournalEntry,
    JournalLine,
    LedgerAccount,
)


def account(code):
    try:
        return LedgerAccount.objects.get(
            code=code,
            is_active=True,
        )
    except LedgerAccount.DoesNotExist:
        raise ValidationError(
            f"Ledger account {code} is missing."
        )


@transaction.atomic
def post_verified_investor_contribution(
    contribution_id,
    verified_by,
):
    contribution = (
    CapitalContribution.objects
    .select_for_update(of=("self",))
    .select_related("investor")
    .get(pk=contribution_id)
)

    if contribution.source_type != "investor":
        raise ValidationError(
            "This service only handles investor contributions."
        )

    if not contribution.investor_id:
        raise ValidationError(
            "The investor contribution has no investor."
        )

    if contribution.verified:
        raise ValidationError(
            "This contribution has already been verified."
        )

    if contribution.amount <= Decimal("0.00"):
        raise ValidationError("Invalid contribution amount.")

    if hasattr(contribution, "capital_entry"):
        raise ValidationError(
            "This contribution already has a capital entry."
        )

    # Lock the investor against concurrent capital operations.
    from .models import Investor

    investor = Investor.objects.select_for_update().get(
        pk=contribution.investor_id
    )

    period = FundingPeriod.objects.select_for_update().filter(
        start_date__lte=contribution.received_at.date(),
        end_date__gte=contribution.received_at.date(),
        status="open",
    ).first()

    if period is None:
        raise ValidationError(
            "No open accounting period covers this contribution."
        )

    amount = Decimal(contribution.amount).quantize(
        Decimal("0.01")
    )

    # Create the journal entry.
    journal = JournalEntry.objects.create(
        reference=f"JRN-{contribution.reference}",
        period=period,
        description="Verified investor capital contribution",
        status="draft",
    )

    # Debit operating cash.
    JournalLine.objects.create(
        entry=journal,
        account=account("1000"),
        debit=amount,
        credit=Decimal("0.00"),
        description="Investor capital received",
    )

    # Credit investor capital.
    JournalLine.objects.create(
        entry=journal,
        account=account("2000"),
        debit=Decimal("0.00"),
        credit=amount,
        description=f"Capital: {investor.legal_name}",
    )

    from .accounting import post_journal_entry

    post_journal_entry(journal)

    # Create the single capital-ledger record.
    entry = InvestorCapitalEntry.objects.create(
        investor=investor,
        entry_type="contribution",
        amount=amount,
        effective_date=contribution.received_at.date(),
        reference=f"CAP-{contribution.reference}",
        source_contribution=contribution,
        journal_entry=journal,
        description="Verified investor capital contribution",
        posted_at=timezone.now(),
    )

    # Mark the source receipt verified only after successful posting.
    contribution.verified = True
    contribution.verified_by = verified_by
    contribution.verified_at = timezone.now()
    contribution.journal_reference = journal.reference
    contribution.save(
        update_fields=[
            "verified",
            "verified_by",
            "verified_at",
            "journal_reference",
        ]
    )

    return entry
