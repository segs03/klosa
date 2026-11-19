from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from backoffice.audit import log as audit_log
from .accounting import get_ledger_account, post_journal_entry
from .models import (CapitalContribution, FundingPeriod, Investor, InvestorCapitalEntry,
                     JournalEntry, JournalLine)
from .security import need


@transaction.atomic
def post_verified_investor_contribution(contribution_id, verified_by):
    need(verified_by, "funding.verify_capitalcontribution")

    contribution = (CapitalContribution.objects.select_for_update(of=("self",))
                    .select_related("investor").get(pk=contribution_id))

    if contribution.source_type != "investor":
        raise ValidationError("This service only handles investor contributions.")
    if not contribution.investor_id:
        raise ValidationError("The investor contribution has no investor.")
    if contribution.verified:
        raise ValidationError("This contribution has already been verified.")
    if contribution.amount <= Decimal("0.00"):
        raise ValidationError("Invalid contribution amount.")
    if hasattr(contribution, "capital_entry"):
        raise ValidationError("This contribution already has a capital entry.")
    if contribution.recorded_by_id is None:
        raise ValidationError("This contribution has no recorder on record.")
    if contribution.recorded_by_id == verified_by.pk:
        raise PermissionDenied("The person who recorded a contribution cannot verify it.")

    investor = Investor.objects.select_for_update().get(pk=contribution.investor_id)
    day = contribution.received_at.date()
    periods = list(FundingPeriod.objects.select_for_update().filter(
        start_date__lte=day, end_date__gte=day, status="open"))
    if len(periods) != 1:
        raise ValidationError("Exactly one open accounting period must cover this contribution.")
    period = periods[0]

    amount = Decimal(contribution.amount).quantize(Decimal("0.01"))

    journal = JournalEntry.objects.create(
        reference=f"JRN-{contribution.reference}", period=period,
        description="Verified investor capital contribution", status="draft")
    JournalLine.objects.create(entry=journal, account=get_ledger_account("cash"),
                               debit=amount, credit=Decimal("0.00"), description="Investor capital received")
    JournalLine.objects.create(entry=journal, account=get_ledger_account("investor_capital"),
                               debit=Decimal("0.00"), credit=amount, description=f"Capital: {investor.legal_name}")
    post_journal_entry(journal)

    entry = InvestorCapitalEntry.objects.create(
        investor=investor, entry_type="contribution", amount=amount, effective_date=day,
        reference=f"CAP-{contribution.reference}", source_contribution=contribution, journal_entry=journal,
        description="Verified investor capital contribution", posted_at=timezone.now())

    contribution.verified = True
    contribution.verified_by = verified_by
    contribution.verified_at = timezone.now()
    contribution.journal_reference = journal.reference
    contribution.save(update_fields=["verified", "verified_by", "verified_at", "journal_reference"])

    audit_log(verified_by, "contribution_verified", contribution.reference, str(amount))
    return entry