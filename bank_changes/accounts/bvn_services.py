import re

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .bvn_provider import BVNNotFound, ProviderError, lookup
from .kyc_services import (
    MIN_AGE,
    OPEN,
    _verify,
    age_on,
    clean_id_number,
    id_digest,
    log,
    verified_types,
)
from .models import Account, KYCApplication

# One generic message on purpose: don't tell a caller which part failed.
MISMATCH = "These BVN details do not match your account."


def _tokens(s):
    return {t for t in re.sub(r"[^a-z ]", " ", (s or "").lower()).split() if t}


def _names_match(a, b):
    """BVN names often carry a middle name, so allow subset either way,
    but require at least two shared name parts."""
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) >= 2 and (ta <= tb or tb <= ta)


def _last10(phone):
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else ""


def _matches_account(user, name, dob, phone):
    """A valid BVN only proves the number exists. Tie it to this person:
    - if they already hold a verified ID, name + date of birth must match it;
    - otherwise the BVN's phone must match their verified phone number."""
    prior = (KYCApplication.objects.filter(user=user, status="verified")
             .exclude(document_type="bvn").order_by("-submitted_at").first())
    if prior:
        return prior.date_of_birth == dob and _names_match(prior.legal_name, name)
    mine, theirs = _last10(user.phone_number), _last10(phone)
    return bool(user.phone_verified and mine and mine == theirs)


def _mask_phone(phone):
    digits = re.sub(r"\D", "", phone or "")
    return f"{digits[:3]}****{digits[-4:]}" if len(digits) >= 10 else ""


def verify_bvn(user, raw_bvn):
    """Verify a BVN from the number alone. Returns the details to show the customer."""
    number = clean_id_number("bvn", raw_bvn)

    account = Account.objects.filter(user=user).first()
    if account is None or account.closed_at:
        raise ValidationError("Your account is not open.")
    if "bvn" in verified_types(user):
        raise ValidationError("Your BVN is already verified.")

    # Network call happens OUTSIDE any transaction so no DB locks are held while we wait.
    try:
        data = lookup(number)
    except BVNNotFound:
        raise ValidationError("We could not find this BVN. Check the number and try again.")
    except ProviderError:
        raise ValidationError("BVN check is unavailable right now. Please try again shortly.")

    name = " ".join(p for p in (data["first_name"], data["middle_name"], data["last_name"]) if p)[:150]
    dob = data["date_of_birth"]
    if not name:
        raise ValidationError("We could not read the details for this BVN.")
    if age_on(dob, timezone.localdate()) < MIN_AGE:
        raise ValidationError("You must be 18 or older.")
    if not _matches_account(user, name, dob, data["phone"]):
        log.info("BVN mismatch for user %s", user.pk)  # never log the number or the details
        raise ValidationError(MISMATCH)

    app = _record(user, number, name, dob)
    log.info("KYC %s: bvn verified by number for user %s", app.pk, user.pk)
    return {"bvn": "•" * 7 + number[-4:], "name": name, "dob": dob.strftime("%d %b %Y"),
            "phone": _mask_phone(data["phone"])}


@transaction.atomic
def _record(user, number, name, dob):
    fields = dict(country_code="NG", document_type="bvn", legal_name=name,
                  date_of_birth=dob, identity_digest=id_digest("bvn", number))

    # Reuse an older image-based BVN request if one is still open, and drop its picture.
    app = (KYCApplication.objects.select_for_update()
           .filter(user=user, document_type="bvn", status__in=OPEN).first())
    old_file = ""
    if app:
        old_file = app.identity_document.name
        for k, v in fields.items():
            setattr(app, k, v)
        app.identity_document = ""
    else:
        app = KYCApplication(user=user, **fields)
    app.status = "pending"
    app.save()

    _verify(app)  # same checks as the staff path: duplicates, age, account, tier refresh
    app.save(update_fields=["status", "updated_at"])

    from .bonus import grant_welcome_bonus
    grant_welcome_bonus(app.user)  # pays only when both NIN and BVN are verified

    if old_file:
        app.identity_document.storage.delete(old_file)
    return app
