import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from backoffice.audit import log as audit_log
from .models import (CapitalContribution, ContributionIntent, FundingPeriod, Investor,
                     InvestorCapitalEntry, JournalEntry, JournalLine, LedgerAccount, LedgerPosting)
from .security import need

MIN_AMOUNT, MAX_AMOUNT, OPEN_LIMIT, EXPIRY_DAYS = Decimal("10000"), Decimal("1000000000"), 3, 7


def _amount(value):
    try:
        amount = Decimal(str(value).strip())
        ok = amount.is_finite() and amount == amount.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        ok = False
    if not ok:
        raise ValidationError("Enter a valid amount with at most 2 decimal places.")
    if not MIN_AMOUNT <= amount <= MAX_AMOUNT:
        raise ValidationError(f"The amount must be between ₦{MIN_AMOUNT:,.0f} and ₦{MAX_AMOUNT:,.0f}.")
    return amount


def create_intent(user, amount, method):
    investor = Investor.objects.filter(user=user, is_active=True).first()
    if investor is None:
        raise ValidationError("You are not an active investor.")
    if method not in dict(ContributionIntent.METHODS):
        raise ValidationError("Choose how you will pay.")
    amount = _amount(amount)
    with transaction.atomic():
        Investor.objects.select_for_update().get(pk=investor.pk)
        now = timezone.now()
        ContributionIntent.objects.filter(investor=investor, status="pending", expires_at__lt=now).update(status="expired")
        if ContributionIntent.objects.filter(investor=investor, status="pending").count() >= OPEN_LIMIT:
            raise ValidationError(f"You can have {OPEN_LIMIT} open deposit notices. Wait for one to be handled.")
        return ContributionIntent.objects.create(
            investor=investor, amount=amount, method=method, reference="IN" + uuid.uuid4().hex[:10].upper(),
            expires_at=now + timedelta(days=EXPIRY_DAYS))


@transaction.atomic
def confirm_received(intent_pk, staff):
    """Funds arrived. This records the contribution; a different person must verify it.
    A notice that expired can still be confirmed, because the money may arrive late."""
    need(staff, "funding.confirm_contributionintent")
    try:
        intent = (ContributionIntent.objects.select_for_update(of=("self",))
                  .select_related("investor").get(pk=intent_pk))
    except ContributionIntent.DoesNotExist:
        raise ValidationError("Notice not found.")
    if intent.status not in ("pending", "expired"):
        raise ValidationError("This notice was already handled.")
    if intent.investor.user_id == staff.pk:
        raise PermissionDenied("You cannot confirm your own deposit.")
    if not intent.investor.is_active:
        raise ValidationError("This investor is not active.")
    now = timezone.now()
    contribution = CapitalContribution.objects.create(
        source_type="investor", investor=intent.investor, amount=intent.amount,
        reference=intent.reference, received_at=now, recorded_by=staff)
    intent.status, intent.contribution, intent.handled_by, intent.handled_at = "received", contribution, staff, now
    intent.save(update_fields=["status", "contribution", "handled_by", "handled_at"])
    audit_log(staff, "intent_confirmed", intent.reference, str(intent.amount))
    return intent


@transaction.atomic
def reject_intent(intent_pk, staff, reason):
    need(staff, "funding.confirm_contributionintent")
    reason = " ".join((reason or "").split())
    if not reason:
        raise ValidationError("Give a reason the investor can read.")
    try:
        intent = ContributionIntent.objects.select_for_update().get(pk=intent_pk)
    except ContributionIntent.DoesNotExist:
        raise ValidationError("Notice not found.")
    if intent.status not in ("pending", "expired"):
        raise ValidationError("This notice was already handled.")
    intent.status, intent.note, intent.handled_by, intent.handled_at = "rejected", reason[:200], staff, timezone.now()
    intent.save(update_fields=["status", "note", "handled_by", "handled_at"])
    audit_log(staff, "intent_rejected", intent.reference, reason)
    return intent


# ---------------------------------------------------------------------------
# Ledger: NEW. Everything above is your original code, unchanged.
# ---------------------------------------------------------------------------

PERM_VERIFY = "funding.verify_capitalcontribution"
CASH_ACCOUNT = "1000"
CAPITAL_ACCOUNT = {"investor": "2000", "company": "2100"}


@transaction.atomic
def verify_contribution(contribution_pk, staff):
    """A second person confirms the contribution, which posts it to the ledger:
    Dr Operating cash / Cr Investor (or Company) capital, plus the investor's capital entry."""
    need(staff, PERM_VERIFY)
    try:
        c = (CapitalContribution.objects.select_for_update(of=("self",))
             .select_related("investor").get(pk=contribution_pk))
    except CapitalContribution.DoesNotExist:
        raise ValidationError("Contribution not found.")
    if c.verified:
        raise ValidationError("This contribution is already verified.")
    if c.recorded_by_id == staff.pk:
        raise PermissionDenied("The person who recorded a contribution cannot verify it.")
    if c.investor_id and c.investor.user_id == staff.pk:
        raise PermissionDenied("You cannot verify your own deposit.")

    entry = _post_contribution(c, staff)
    audit_log(staff, "contribution_verified", c.reference, entry.reference)
    return entry


def _post_contribution(c, staff):
    now = timezone.now()
    day = timezone.localdate(c.received_at)

    period = FundingPeriod.objects.filter(start_date__lte=day, end_date__gte=day).first()
    if period is None or period.status != "open":
        raise ValidationError("No open funding period covers the received date.")

    cash = LedgerAccount.objects.get(code=CASH_ACCOUNT, is_active=True)
    capital = LedgerAccount.objects.get(code=CAPITAL_ACCOUNT[c.source_type], is_active=True)

    # Unique (source_type, source_id) is the hard stop against double posting.
    posting, created = LedgerPosting.objects.get_or_create(source_type="contribution", source_id=c.pk)
    if not created:
        raise ValidationError("This contribution has already been posted.")

    entry = JournalEntry.objects.create(
        reference=f"JE-CONTRIB-{c.pk}", period=period, description=f"Capital contribution {c.reference}")
    JournalLine.objects.create(entry=entry, account=cash, debit=c.amount, description=c.reference)
    JournalLine.objects.create(entry=entry, account=capital, credit=c.amount, description=c.reference)

    totals = entry.lines.aggregate(d=Sum("debit"), cr=Sum("credit"))
    if totals["d"] != totals["cr"]:
        raise ValidationError("Journal entry is not balanced.")

    entry.status, entry.posted_at = "posted", now
    entry.save()
    posting.entry = entry
    posting.save()

    if c.source_type == "investor":
        InvestorCapitalEntry.objects.create(
            investor=c.investor, entry_type="contribution", amount=c.amount, effective_date=day,
            reference=f"CAP-{c.reference}", source_contribution=c, journal_entry=entry,
            description=f"Contribution {c.reference}", posted_at=now)

    c.verified, c.verified_by, c.verified_at = True, staff, now
    c.journal_reference = entry.reference
    c.save()
    return entry


