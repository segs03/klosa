import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from backoffice.audit import log as audit_log
from .models import (
    CapitalContribution,
    ContributionIntent,
    FundingPeriod,
    Investor,
    InvestorCapitalEntry,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    LedgerPosting,
)
from .security import need



MIN_AMOUNT, MAX_AMOUNT, OPEN_LIMIT, EXPIRY_DAYS = Decimal("10000"), Decimal("1000000000"), 3, 7

# ASSUMPTION: adjust the app label to match your project.
PERM_CONFIRM = "funding.confirm_contributionintent"
PERM_VERIFY = "funding.verify_capitalcontribution"

CASH_ACCOUNT = "1000"
CAPITAL_ACCOUNT = {"investor": "2000", "company": "2100"}


def _amount(value):
    try:
        amount = Decimal(str(value).strip())
        ok = amount.is_finite() and amount == amount.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        ok = False
    if not ok:
        raise ValidationError("Amount must be a number with at most 2 decimal places.")
    if not (MIN_AMOUNT <= amount <= MAX_AMOUNT):
        raise ValidationError(f"Amount must be between {MIN_AMOUNT} and {MAX_AMOUNT}.")
    return amount.quantize(Decimal("0.01"))


def _audit(user, action, obj, **details):
    # ASSUMPTION: single place to adapt to backoffice.audit.log's real signature.
    audit_log(user, action, obj, **details)


def _new_reference():
    return "CI-" + uuid.uuid4().hex[:12].upper()  # 15 chars, column allows 20


def _lock_pending(intent_id):
    intent = (
        ContributionIntent.objects.select_for_update()
        .select_related("investor")
        .get(pk=intent_id)
    )
    if intent.status != "pending":
        raise ValidationError(f"This intent is already {intent.get_status_display().lower()}.")
    return intent


# ---------------------------------------------------------------- intents

@transaction.atomic
def create_intent(user, amount, method, note=""):
    try:
        # Row lock serialises concurrent requests so the open limit can't be raced.
        investor = Investor.objects.select_for_update().get(user=user)
    except Investor.DoesNotExist:
        raise PermissionDenied("No investor profile for this user.")
    if not investor.is_active:
        raise PermissionDenied("Investor account is inactive.")
    if method not in dict(ContributionIntent.METHODS):
        raise ValidationError("Unknown payment method.")

    amount = _amount(amount)
    expire_stale_intents(investor)

    if investor.intents.filter(status="pending").count() >= OPEN_LIMIT:
        raise ValidationError(f"You can have at most {OPEN_LIMIT} open contribution requests.")

    intent = ContributionIntent.objects.create(
        investor=investor,
        amount=amount,
        method=method,
        reference=_new_reference(),
        note=(note or "").strip()[:200],
        expires_at=timezone.now() + timedelta(days=EXPIRY_DAYS),
    )
    _audit(user, "intent.created", intent, amount=str(amount), method=method)
    return intent


def expire_stale_intents(investor=None):
    """Also call from a scheduled job / management command."""
    qs = ContributionIntent.objects.filter(status="pending", expires_at__lte=timezone.now())
    if investor is not None:
        qs = qs.filter(investor=investor)
    return qs.update(status="expired")


@transaction.atomic
def reject_intent(user, intent_id, note=""):
    need(user, PERM_CONFIRM)
    intent = _lock_pending(intent_id)
    intent.status = "rejected"
    intent.handled_by, intent.handled_at = user, timezone.now()
    if note:
        intent.note = note.strip()[:200]
    intent.save()
    _audit(user, "intent.rejected", intent)
    return intent


@transaction.atomic
def confirm_intent(user, intent_id, received_at=None):
    """Staff confirms funds arrived. Records an UNVERIFIED contribution; posting
    to the ledger happens in verify_contribution (second person)."""
    need(user, PERM_CONFIRM)
    intent = _lock_pending(intent_id)
    now = timezone.now()

    # POLICY: late funds on an expired-but-unswept intent are refused here.
    if intent.expires_at <= now:
        raise ValidationError("This intent has expired. Ask the investor to create a new one.")

    received_at = received_at or now
    if received_at > now:
        raise ValidationError("Received time cannot be in the future.")

    contribution = CapitalContribution.objects.create(
        source_type="investor",
        investor=intent.investor,
        amount=intent.amount,
        reference=intent.reference,  # unique on both tables, so reuse it
        received_at=received_at,
        recorded_by=user,
    )
    intent.status = "received"
    intent.contribution = contribution
    intent.handled_by, intent.handled_at = user, now
    intent.save()
    _audit(user, "intent.confirmed", intent, contribution_id=contribution.pk)
    return contribution


# ----------------------------------------------------------------- ledger

@transaction.atomic
def verify_contribution(user, contribution_id):
    need(user, PERM_VERIFY)
    c = CapitalContribution.objects.select_for_update().get(pk=contribution_id)
    if c.verified:
        raise ValidationError("Contribution is already verified.")
    if c.recorded_by_id == user.pk:
        raise PermissionDenied("The person who recorded a contribution cannot verify it.")

    entry = _post_contribution(c, user)
    _audit(user, "contribution.verified", c, journal=entry.reference)
    return entry


def _post_contribution(c, user):
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
        reference=f"JE-CONTRIB-{c.pk}",
        period=period,
        description=f"Capital contribution {c.reference}",
    )
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
            investor=c.investor,
            entry_type="contribution",
            amount=c.amount,
            effective_date=day,
            reference=f"CAP-{c.reference}",
            source_contribution=c,
            journal_entry=entry,
            description=f"Contribution {c.reference}",
            posted_at=now,
        )

    c.verified, c.verified_by, c.verified_at = True, user, now
    c.journal_reference = entry.reference
    c.save()
    return entry
