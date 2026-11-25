import logging
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Account, AddressProof

log = logging.getLogger("kyc.audit")

MAX_BILL_AGE_DAYS = 90
MAX_SUBMISSIONS = 3          # per 30 days, so nobody can keep guessing


def _text(value, label, low, high):
    clean = " ".join((value or "").split())
    if not (low <= len(clean) <= high):
        raise ValidationError(f"Enter your {label}.")
    return clean


@transaction.atomic
def submit_address(user, *, line1, line2="", city, state, bill_date, document):
    line1 = _text(line1, "street address", 5, 150)
    line2 = " ".join((line2 or "").split())[:150]
    city = _text(city, "city", 2, 100)
    state = _text(state, "state", 2, 100)

    today = timezone.localdate()
    if bill_date is None or bill_date > today:
        raise ValidationError("The bill date cannot be in the future.")
    if (today - bill_date).days > MAX_BILL_AGE_DAYS:
        raise ValidationError("Use a bill from the last 3 months.")

    account = Account.objects.select_for_update().filter(user=user).first()   # one submission at a time
    if account is None or account.closed_at:
        raise ValidationError("Your account is not open.")
    if AddressProof.objects.filter(user=user, status="approved").exists():
        raise ValidationError("Your address is already verified.")
    if AddressProof.objects.filter(user=user, status="pending").exists():
        raise ValidationError("Your address is already being reviewed.")
    recent = AddressProof.objects.filter(user=user, submitted_at__gte=timezone.now() - timedelta(days=30)).count()
    if recent >= MAX_SUBMISSIONS:
        raise ValidationError("You have reached the limit of 3 submissions in 30 days. Contact support.")

    try:
        with transaction.atomic():
            return AddressProof.objects.create(
                user=user, line1=line1, line2=line2, city=city, state=state,
                bill_date=bill_date, document=document)
    except IntegrityError:
        raise ValidationError("Your address is already being reviewed.")


@transaction.atomic
def review_address(proof_id, reviewer, decision, reason=""):
    if not (reviewer and reviewer.is_authenticated and reviewer.is_active and reviewer.is_staff
            and reviewer.has_perm("accounts.review_addressproof")):
        raise PermissionDenied("You are not allowed to review addresses.")
    if decision not in ("approved", "rejected"):
        raise ValidationError("Choose a decision.")
    reason = (reason or "").strip()
    if len(reason) > 500:
        raise ValidationError("Give a reason of up to 500 characters.")
    if decision == "rejected" and not reason:
        raise ValidationError("Say why the address was not accepted.")

    proof = AddressProof.objects.select_for_update().select_related("user").get(pk=proof_id)
    if proof.user_id == reviewer.pk:
        raise PermissionDenied("You cannot review your own address.")
    if proof.status != "pending":
        raise ValidationError("This request is not waiting for review.")

    proof.status, proof.reason = decision, reason
    proof.reviewed_by, proof.reviewed_at = reviewer, timezone.now()
    proof.save()

    if decision == "approved":
        from .tiers import refresh_tier
        refresh_tier(proof.user)                       # may raise the customer's tier
    log.info("Address %s: %s by user %s", proof.pk, decision, reviewer.pk)
    return proof
