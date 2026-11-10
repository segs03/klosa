from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError, PermissionDenied
from django.conf import settings
import secrets

from cards.models import BankCard

from uuid import uuid4

from transactions.models import CreditTransaction

from .models import (
    Account,
    CreditAccount,
    CreditPolicy,
    CreditApplication,
    VerifiedIdentity,
)


# Demo approval policy
STARTER_LIMIT = Decimal("250000.00")
STANDARD_LIMIT = Decimal("500000.00")



@transaction.atomic
def approve_credit(account):
    account = Account.objects.select_for_update().get(
        pk=account.pk
    )

    credit, created = CreditAccount.objects.get_or_create(
        account=account
    )

    if not created and credit.status == "active":
        return credit

    age_days = (timezone.now() - account.created_at).days

    if age_days < 30:
        raise ValidationError(
            "Your account must be at least 30 days old."
        )

    if account.balance < Decimal("10000.00"):
        raise ValidationError(
            "Your account does not meet the demo eligibility criteria."
        )

    # Determine the account's credit level.
    is_verified = VerifiedIdentity.objects.filter(
        user=account.user
    ).exists()

    account_level = "verified" if is_verified else "basic"

    # Retrieve the active policy for this account level and currency.
    policy = CreditPolicy.objects.filter(
        account_level=account_level,
        currency=account.currency,
        is_active=True,
    ).first()

    if policy is None:
        raise ValidationError(
            f"No active credit policy is configured for "
            f"{account_level} accounts in {account.currency}."
        )

    policy.full_clean()

    # Apply the configured maximum as the demo credit limit.
    approved_limit = policy.maximum_limit

    if approved_limit < policy.minimum_limit:
        raise ValidationError(
            "The configured credit limit is outside the policy range."
        )

    credit.credit_limit = approved_limit
    credit.status = "active"
    credit.approved_at = timezone.now()
    credit.save()

    BankCard.objects.get_or_create(
        account=account,
        card_type="credit",
        defaults={
            "cardholder_name": (
                account.user.get_full_name()
                or account.user.username
            ),
            "last_four": str(
                secrets.randbelow(10000)
            ).zfill(4),
            "expiry_month": timezone.now().month,
            "expiry_year": timezone.now().year + 5,
            "status": "active",
        },
    )

    return credit




@transaction.atomic
def make_credit_purchase(credit_account, amount, description="Card purchase"):
    amount = Decimal(str(amount))

    if not amount.is_finite() or amount <= 0:
        raise ValidationError("Enter a valid purchase amount.")

    credit = CreditAccount.objects.select_for_update().get(
        pk=credit_account.pk
    )

    card = CreditCard.objects.get(credit_account=credit)

    if credit.status != "active" or card.status != "active":
        raise ValidationError("Your credit facility or card is inactive.")

    if amount > credit.available_credit:
        raise ValidationError("Insufficient available credit.")

    credit.outstanding_balance += amount
    credit.save(update_fields=["outstanding_balance"])

    return CreditTransaction.objects.create(
        credit_account=credit,
        transaction_type="purchase",
        amount=amount,
        reference="KLC" + uuid4().hex[:16].upper(),
        description=description,
    )


@transaction.atomic
def repay_credit(credit_account, amount):
    amount = Decimal(str(amount))

    if not amount.is_finite() or amount <= 0:
        raise ValidationError("Enter a valid repayment amount.")

    credit = CreditAccount.objects.select_for_update().get(
        pk=credit_account.pk
    )

    account = Account.objects.select_for_update().get(
        pk=credit.account_id
    )

    if credit.status != "active":
        raise ValidationError("Your credit facility is inactive.")

    if amount > credit.outstanding_balance:
        raise ValidationError("Repayment exceeds your outstanding balance.")

    if amount > account.balance:
        raise ValidationError("Insufficient account balance for repayment.")

    account.balance -= amount
    account.save(update_fields=["balance"])

    credit.outstanding_balance -= amount
    credit.save(update_fields=["outstanding_balance"])

    return CreditTransaction.objects.create(
        credit_account=credit,
        transaction_type="repayment",
        amount=amount,
        reference="KLR" + uuid4().hex[:16].upper(),
        description="Credit card repayment",
    )
    
    
    

def _get_credit_policy(account):
    account_level = (
        "verified"
        if VerifiedIdentity.objects.filter(
            user=account.user
        ).exists()
        else "basic"
    )

    policy = CreditPolicy.objects.filter(
        account_level=account_level,
        currency=account.currency,
        is_active=True,
    ).first()

    if policy is None:
        raise ValidationError(
            f"No active credit policy exists for "
            f"{account_level} accounts in {account.currency}."
        )

    policy.full_clean()
    return policy


def _check_credit_staff(staff, permission):
    if (
        not staff
        or not staff.is_authenticated
        or not staff.is_active
        or not staff.is_staff
        or not staff.has_perm(permission)
    ):
        raise PermissionDenied(
            "You are not authorised to perform this credit action."
        )


def _validate_reason(reason):
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError("A decision reason is required.")

    if len(reason.strip()) > 500:
        raise ValidationError(
            "The reason cannot exceed 500 characters."
        )

    return reason.strip()


@transaction.atomic
def submit_credit_application(*, user, requested_amount):
    if not user or not user.is_authenticated:
        raise PermissionDenied(
            "You must be logged in to apply for credit."
        )

    try:
        amount = Decimal(str(requested_amount))
    except Exception:
        raise ValidationError("Enter a valid credit amount.")

    if not amount.is_finite() or amount <= 0:
        raise ValidationError(
            "The requested amount must be greater than zero."
        )

    account = (
        Account.objects
        .select_for_update()
        .filter(user=user)
        .first()
    )

    if account is None:
        raise ValidationError("You do not have an account.")

    if CreditAccount.objects.filter(
        account=account,
        status="active",
    ).exists():
        raise ValidationError(
            "You already have an active credit facility."
        )

    policy = _get_credit_policy(account)

    if not policy.minimum_limit <= amount <= policy.maximum_limit:
        raise ValidationError(
            "The requested amount is outside the permitted credit policy range."
        )

    return CreditApplication.objects.create(
        user=user,
        requested_amount=amount,
    )


@transaction.atomic
def assess_credit_application(
    *,
    application_id,
    assessor,
    assessed_amount,
    reason,
):
    _check_credit_staff(
        assessor,
        "accounts.assess_creditapplication",
    )

    reason = _validate_reason(reason)

    try:
        amount = Decimal(str(assessed_amount))
    except Exception:
        raise ValidationError("Enter a valid assessed amount.")

    if not amount.is_finite() or amount <= 0:
        raise ValidationError(
            "The assessed amount must be greater than zero."
        )

    application = (
        CreditApplication.objects
        .select_for_update()
        .get(pk=application_id)
    )

    if application.status != "pending":
        raise ValidationError(
            "This application is not awaiting assessment."
        )

    if application.user_id == assessor.pk:
        raise PermissionDenied(
            "You cannot assess your own credit application."
        )

    account = Account.objects.select_for_update().get(
        user=application.user
    )

    age_days = (timezone.now() - account.created_at).days

    if age_days < 30:
        raise ValidationError(
            "The account must be at least 30 days old."
        )

    if account.balance < Decimal("10000.00"):
        raise ValidationError(
            "The account does not meet the demo eligibility criteria."
        )

    policy = _get_credit_policy(account)

    if not policy.minimum_limit <= amount <= policy.maximum_limit:
        raise ValidationError(
            "The assessed amount is outside the credit policy range."
        )

    if amount > application.requested_amount:
        raise ValidationError(
            "The assessed amount cannot exceed the requested amount."
        )

    application.assessed_amount = amount
    application.assessment_reason = reason
    application.assessed_by = assessor
    application.assessed_at = timezone.now()
    application.status = "assessed"

    application.save(
        update_fields=[
            "assessed_amount",
            "assessment_reason",
            "assessed_by",
            "assessed_at",
            "status",
        ]
    )

    return application


@transaction.atomic
def decide_credit_application(
    *,
    application_id,
    approver,
    decision,
    reason,
):
    _check_credit_staff(
        approver,
        "accounts.approve_creditapplication",
    )

    reason = _validate_reason(reason)

    if decision not in ["approved", "rejected"]:
        raise ValidationError(
            "Decision must be approved or rejected."
        )

    application = (
        CreditApplication.objects
        .select_for_update()
        .get(pk=application_id)
    )

    if application.status != "assessed":
        raise ValidationError(
            "This application is not awaiting a decision."
        )

    if application.assessed_by_id == approver.pk:
        raise PermissionDenied(
            "The assessor cannot approve or reject the same application."
        )

    if decision == "rejected":
        application.status = "rejected"
        application.approved_by = approver
        application.decision_reason = reason
        application.decided_at = timezone.now()

        application.save(
            update_fields=[
                "status",
                "approved_by",
                "decision_reason",
                "decided_at",
            ]
        )

        return application

    account = Account.objects.select_for_update().get(
        user=application.user
    )

    policy = _get_credit_policy(account)

    amount = application.assessed_amount

    if amount is None:
        raise ValidationError(
            "This application has no assessed credit amount."
        )

    if not policy.minimum_limit <= amount <= policy.maximum_limit:
        raise ValidationError(
            "The assessed amount no longer meets the active credit policy."
        )

    credit, created = CreditAccount.objects.get_or_create(
        account=account,
    )

    if not created and credit.status == "active":
        raise ValidationError(
            "This account already has an active credit facility."
        )

    credit.credit_limit = amount
    credit.status = "active"
    credit.approved_at = timezone.now()
    credit.save(
        update_fields=[
            "credit_limit",
            "status",
            "approved_at",
        ]
    )

    application.status = "approved"
    application.approved_by = approver
    application.decision_reason = reason
    application.decided_at = timezone.now()

    application.save(
        update_fields=[
            "status",
            "approved_by",
            "decision_reason",
            "decided_at",
        ]
    )

    return application
