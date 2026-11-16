import hashlib
import hmac
import re

from django.conf import settings
from django.core.exceptions import ValidationError, PermissionDenied
from django.utils import timezone

from .models import (
    KYCApplication,
    KYCReview,
    VerifiedIdentity,
    Account,
    VerifiedDocument
)

import logging
from datetime import timedelta

from django.db import IntegrityError, transaction

from .utils import digest




log = logging.getLogger("kyc.audit")


ZERO_WIDTH = "\u200b\u200c\u200d\u200e\u200f\u2060\ufeff"
DOC_TYPES = {
    "nin": ("National ID number (NIN)", r"[0-9]{11}"),
    "bvn": ("Bank verification number (BVN)", r"[0-9]{11}"),
    "passport": ("International passport number", r"[A-Z0-9]{7,12}"),
    "drivers_license": ("Driver's licence number", r"[A-Z0-9]{8,16}"),
}


def clean_id_number(doc_type, raw):
    label, pattern = DOC_TYPES[doc_type]
    number = re.sub(r"[\s\-" + ZERO_WIDTH + r"]", "", raw or "").upper()    # spaces, dashes and hidden characters from pasting
    if re.fullmatch(pattern, number):
        return number
    if not number:
        raise ValidationError("Enter your ID number.")
    if doc_type in ("nin", "bvn"):
        short = doc_type.upper()
        if not (number.isascii() and number.isdigit()):
            raise ValidationError(f"A {short} has digits only. Remove any letters or symbols.")
        raise ValidationError(f"A {short} is exactly 11 digits. You entered {len(number)}.")
    raise ValidationError(f"Enter a valid {label}.")
    
OPEN = ("pending", "under_review", "additional_info")
MIN_AGE = 18


def age_on(dob, today):
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def id_digest(doc_type, number):
    return digest("NG", doc_type, number, key=getattr(settings, "IDENTITY_HASH_KEY", "") or None)


def verified_types(user):
    types = set(VerifiedDocument.objects.filter(user=user).values_list("document_type", flat=True))
    legacy = VerifiedIdentity.objects.filter(user=user).values_list("document_type", flat=True).first()
    if legacy:
        types.add(legacy)
    return types


@transaction.atomic
def submit_application(user, *, doc_type, id_number, legal_name, date_of_birth, document):
    if doc_type not in DOC_TYPES:
        raise ValidationError("Choose an ID type.")
    number = clean_id_number(doc_type, id_number)
    name = " ".join((legal_name or "").split())
    if not re.fullmatch(r"[A-Za-z][A-Za-z .'\-]{2,148}", name):
        raise ValidationError("Enter your full legal name, letters only.")
    today = timezone.localdate()
    years = age_on(date_of_birth, today)
    if date_of_birth > today or years > 120:
        raise ValidationError("Check your date of birth.")
    if years < MIN_AGE:
        raise ValidationError("You must be 18 or older.")

    account = Account.objects.select_for_update().filter(user=user).first()    # one submission at a time per user
    if account is None or account.closed_at:
        raise ValidationError("Your account is not open.")
    if doc_type in verified_types(user):
        raise ValidationError("This ID is already verified.")
    current = KYCApplication.objects.select_for_update().filter(user=user, status__in=OPEN).first()
    if current and current.status != "additional_info":
        raise ValidationError("Your request is already being reviewed.")
    recent = (KYCApplication.objects.filter(user=user, submitted_at__gte=timezone.now() - timedelta(days=30))
              .exclude(status="verified").count())
    if not current and recent >= 3:
        raise ValidationError("You have reached the limit of 3 submissions in 30 days. Contact support.")

    # An ID that already belongs to another account is NOT refused here: that would let anyone
    # test which IDs are registered. The reviewer sees the warning and cannot approve it.
    fields = dict(country_code="NG", document_type=doc_type, legal_name=name,
                  date_of_birth=date_of_birth, identity_digest=id_digest(doc_type, number))
    if current:                                           # the customer is answering a request for more information
        old = current.identity_document.name
        for k, v in fields.items():
            setattr(current, k, v)
        current.identity_document = document
        current.status = "pending"
        current.save()
        if old and old != current.identity_document.name:
            current.identity_document.storage.delete(old)
        return current
    return KYCApplication.objects.create(user=user, identity_document=document, **fields)
    

def duplicate_hints(app):
    return {
         "same_id": (VerifiedIdentity.objects.filter(identity_digest=app.identity_digest).exclude(user_id=app.user_id).exists()
                    or VerifiedDocument.objects.filter(identity_digest=app.identity_digest).exclude(user_id=app.user_id).exists()),
        "same_name_dob": KYCApplication.objects.filter(
            status="verified", legal_name__iexact=app.legal_name, date_of_birth=app.date_of_birth
        ).exclude(user_id=app.user_id).count(),
    }


def _verify(app):
    if age_on(app.date_of_birth, timezone.localdate()) < MIN_AGE:
        raise ValidationError("The applicant is under 18.")
    account = Account.objects.select_for_update().filter(user_id=app.user_id).first()
    if account is None or account.closed_at:
        raise ValidationError("The account is not open.")
    taken = "This ID is already verified on another account, or this person is already verified."
    if app.document_type in verified_types(app.user):
        raise ValidationError(taken)
    if VerifiedIdentity.objects.filter(identity_digest=app.identity_digest).exclude(user_id=app.user_id).exists():
        raise ValidationError(taken)
    try:
        with transaction.atomic():                        # a savepoint, so the error is catchable
            VerifiedDocument.objects.create(user_id=app.user_id, document_type=app.document_type,
                                            identity_digest=app.identity_digest, verified_at=timezone.now(),
                                            source_application=app)
            if not VerifiedIdentity.objects.filter(user_id=app.user_id).exists():
                VerifiedIdentity.objects.create(
                    user_id=app.user_id, country_code=app.country_code, document_type=app.document_type,
                    identity_digest=app.identity_digest, verified_at=timezone.now(), source_application=app)
    except IntegrityError:
        raise ValidationError(taken)
    app.status = "verified"
    if account.kyc_tier < 1:
        account.kyc_tier = 1
        account.save(update_fields=["kyc_tier"])
    user = app.user
    if not (user.first_name or user.last_name):           # receipts then show a real name
        first, _, last = app.legal_name.partition(" ")
        user.first_name, user.last_name = first[:150], last[:150]
        user.save(update_fields=["first_name", "last_name"])


@transaction.atomic
def review_application(application_id, reviewer, decision, reason):
    if not (reviewer and reviewer.is_authenticated and reviewer.is_active and reviewer.is_staff
            and reviewer.has_perm("accounts.review_kycapplication")):
        raise PermissionDenied("You are not allowed to review ID requests.")
    if decision not in ("approved", "rejected", "additional_info"):
        raise ValidationError("Choose a decision.")
    reason = (reason or "").strip()
    if not reason or len(reason) > 500:
        raise ValidationError("Give a reason of up to 500 characters.")
    app = KYCApplication.objects.select_for_update().select_related("user").get(pk=application_id)
    if app.status not in ("pending", "under_review"):
        raise ValidationError("This request is not waiting for review.")
    if app.user_id == reviewer.pk:
        raise PermissionDenied("You cannot review your own ID request.")
    if KYCReview.objects.filter(application=app, reviewer=reviewer).exists():
        raise ValidationError("You have already reviewed this request.")

    KYCReview.objects.create(application=app, reviewer=reviewer, decision=decision, reason=reason)
    if decision == "approved":
        needed = getattr(settings, "KYC_APPROVALS_REQUIRED", 1)
        if KYCReview.objects.filter(application=app, decision="approved").count() >= needed:
            _verify(app)
        else:
            app.status = "under_review"
    else:
        app.status = decision                            # "rejected" or "additional_info"
    app.save(update_fields=["status", "updated_at"])
    
    if app.status == "verified":
        from .bonus import grant_welcome_bonus
        grant_welcome_bonus(app.user)                     # pays only when both NIN and BVN are verified
    
    log.info("KYC %s: %s by user %s", app.pk, decision, reviewer.pk)
    return app


def generate_identity_digest(
    *,
    country_code,
    document_type,
    normalized_identifier,
):
    """
    Generate a deterministic, keyed digest.
    The raw identity number is never stored.
    """

    secret = getattr(
        settings,
        "KYC_IDENTITY_HMAC_KEY",
        None,
    )

    if not secret:
        raise ValidationError(
            "KYC identity hashing key is not configured."
        )

    if not normalized_identifier:
        raise ValidationError(
            "An identity reference is required."
        )

    value = "|".join([
        country_code.strip().upper(),
        document_type.strip().lower(),
        normalized_identifier.strip().upper(),
    ])

    return hmac.new(
        secret.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _check_kyc_reviewer(reviewer):
    if (
        not reviewer
        or not reviewer.is_authenticated
        or not reviewer.is_active
        or not reviewer.is_staff
        or not reviewer.has_perm(
            "accounts.review_kycapplication"
        )
    ):
        raise PermissionDenied(
            "You are not authorised to review KYC applications."
        )


@transaction.atomic
def approve_kyc_application(
    *,
    application_id,
    reviewer,
    identity_digest,
):
    _check_kyc_reviewer(reviewer)

    if not re.fullmatch(r"[0-9a-f]{64}", identity_digest or ""):
        raise ValidationError(
            "A valid identity digest is required."
        )

    application = (
        KYCApplication.objects
        .select_for_update()
        .get(pk=application_id)
    )

    if application.status not in ["pending", "under_review"]:
        raise ValidationError(
            "This application is not available for approval."
        )

    if application.user_id == reviewer.pk:
        raise PermissionDenied(
            "You cannot review your own KYC application."
        )

    if KYCReview.objects.filter(
        application=application,
        reviewer=reviewer,
    ).exists():
        raise ValidationError(
            "You have already reviewed this application."
        )
        
    if KYCReview.objects.filter(
    application=application,
    decision__in=["rejected", "additional_info"],
).exists():
        raise ValidationError(
        "This application cannot proceed with approval."
    )

    existing_identity = VerifiedIdentity.objects.filter(
        identity_digest=identity_digest,
    ).first()

    if existing_identity:
        raise ValidationError(
            "This identity is already associated with a Klosa account."
        )

    review_count = KYCReview.objects.filter(
    application=application,
    decision="approved",
).count()

    if review_count >= 2:
        raise ValidationError("This application already has two approvals.")

    if review_count == 0:
        application.identity_digest = identity_digest
        application.save(update_fields=["identity_digest", "updated_at"])

    elif not hmac.compare_digest(
    application.identity_digest,
    identity_digest,
):
        raise ValidationError(
        "The identity submitted does not match the first review."
    )

    review = KYCReview(
    application=application,
    reviewer=reviewer,
    decision="approved",
    reason="Identity verification approved.",
)
    review.full_clean()
    review.save()

    review_count += 1

    if review_count < 2:
        application.status = "under_review"
        application.save(update_fields=["status", "updated_at"])

        return {
            "status": "awaiting_second_approval",
            "application": application,
            "review": review,
        }

    # Second independent approval.
    identity = VerifiedIdentity(
        user=application.user,
        country_code=application.country_code,
        document_type=application.document_type,
        identity_digest=identity_digest,
        verified_at=timezone.now(),
        source_application=application,
    )

    identity.full_clean()
    identity.save()

    application.status = "verified"
    application.save(update_fields=["status", "updated_at"])

    return {
        "status": "verified",
        "application": application,
        "review": review,
        "identity": identity,
    }
    


def _validate_review_reason(reason):
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError("A review reason is required.")

    if len(reason.strip()) > 500:
        raise ValidationError("The review reason cannot exceed 500 characters.")

    return reason.strip()


def _get_reviewable_application(application_id, reviewer):
    _check_kyc_reviewer(reviewer)

    application = (
        KYCApplication.objects
        .select_for_update()
        .get(pk=application_id)
    )

    if application.user_id == reviewer.pk:
        raise PermissionDenied(
            "You cannot review your own KYC application."
        )

    if application.status not in ["pending", "under_review"]:
        raise ValidationError(
            "This application is not available for review."
        )

    if KYCReview.objects.filter(
        application=application,
        reviewer=reviewer,
    ).exists():
        raise ValidationError(
            "You have already reviewed this application."
        )

    return application


@transaction.atomic
def request_kyc_additional_info(
    *,
    application_id,
    reviewer,
    reason,
):
    reason = _validate_review_reason(reason)

    application = _get_reviewable_application(
        application_id,
        reviewer,
    )

    if KYCReview.objects.filter(
        application=application,
        decision="rejected",
    ).exists():
        raise ValidationError(
            "A rejection review has already started."
        )

    if KYCReview.objects.filter(
        application=application,
        decision="approved",
    ).exists():
        raise ValidationError(
            "Approval review has already started."
        )

    review = KYCReview(
        application=application,
        reviewer=reviewer,
        decision="additional_info",
        reason=reason,
    )
    review.full_clean()
    review.save()

    application.status = "additional_info"
    application.save(update_fields=["status", "updated_at"])

    return {
        "status": "additional_info",
        "application": application,
        "review": review,
    }


@transaction.atomic
def reject_kyc_application(
    *,
    application_id,
    reviewer,
    reason,
):
    reason = _validate_review_reason(reason)

    application = _get_reviewable_application(
        application_id,
        reviewer,
    )

    if KYCReview.objects.filter(
        application=application,
        decision="approved",
    ).exists():
        raise ValidationError(
            "Approval review has already started."
        )

    if KYCReview.objects.filter(
        application=application,
        decision="additional_info",
    ).exists():
        raise ValidationError(
            "Additional information has already been requested."
        )

    rejection_count = KYCReview.objects.filter(
        application=application,
        decision="rejected",
    ).count()

    if rejection_count >= 2:
        raise ValidationError(
            "This application already has two rejection reviews."
        )

    review = KYCReview(
        application=application,
        reviewer=reviewer,
        decision="rejected",
        reason=reason,
    )
    review.full_clean()
    review.save()

    rejection_count += 1

    if rejection_count == 1:
        application.status = "under_review"
        application.save(update_fields=["status", "updated_at"])

        return {
            "status": "awaiting_second_rejection",
            "application": application,
            "review": review,
        }

    application.status = "rejected"
    application.save(update_fields=["status", "updated_at"])

    return {
        "status": "rejected",
        "application": application,
        "review": review,
    }
