import hashlib
import hmac
import re

from django.conf import settings
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from .models import (
    KYCApplication,
    KYCReview,
    VerifiedIdentity,
)


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
