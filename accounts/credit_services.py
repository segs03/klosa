
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError, PermissionDenied
from django.conf import settings
import secrets

from uuid import uuid4

from transactions.models import CreditTransaction, Transaction

from .models import (
    Account,
    CreditAccount,
    CreditPolicy,
    CreditApplication,
    VerifiedIdentity,
)

from cards.models import CardOrder, BankCard
from cards.services import issue_virtual_card_order

import logging

logger = logging.getLogger(__name__)



# Demo approval policy
STARTER_LIMIT = Decimal("250000.00")
STANDARD_LIMIT = Decimal("500000.00")



def eligibility_problem(account):
    """None when the account meets the rules, otherwise one plain sentence."""
    min_age = getattr(settings, "CREDIT_MIN_ACCOUNT_AGE_DAYS", 30)
    min_bal = Decimal(str(getattr(settings, "CREDIT_MIN_BALANCE", "10000")))
    age = (timezone.now() - account.created_at).days
    if age < min_age:
        return f"The account must be at least {min_age} days old. It is {age} day(s) old."
    if account.balance < min_bal:
        return f"The balance must be at least ₦{min_bal:,.0f}. It is ₦{account.balance:,.2f}."
    return None


def check_eligibility(account):
    problem = eligibility_problem(account)
    if problem:
        raise ValidationError(problem)


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

    
    check_eligibility(account)

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




CENT = Decimal("0.01")


def _money(value):
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValidationError("Enter a valid amount.")
    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(CENT):
        raise ValidationError("Enter an amount above zero with at most 2 decimal places.")
    return amount


def _lock_credit(credit_pk):
    acc_id = CreditAccount.objects.filter(pk=credit_pk).values_list("account_id", flat=True).first()
    if acc_id is None:
        raise ValidationError("Credit facility not found.")
    account = Account.objects.select_for_update().get(pk=acc_id)       # account first, then credit
    credit = CreditAccount.objects.select_for_update().get(pk=credit_pk)
    return account, credit


def credit_summary(account):
    credit = CreditAccount.objects.filter(account=account, status="active").first()
    if credit is None:
        return None
    limit, owed = credit.credit_limit, credit.outstanding_balance
    return {"credit": credit, "limit": limit, "owed": owed, "available": credit.available_credit,
            "pct": int(min(owed / limit * 100, 100)) if limit else 0, "due": credit.repayment_due_date}


def spend_on_credit(card, amount, *, reference, merchant=""):
    """Authorise a purchase on a credit card. It uses the credit line and never the main balance.
    The same reference twice spends once."""
    amount = _money(amount)
    if not reference or len(reference) > 100:
        raise ValidationError("Missing reference.")
    with transaction.atomic():
        acc_id = BankCard.objects.filter(pk=card.pk).values_list("account_id", flat=True).first()
        facility = CreditAccount.objects.filter(account_id=acc_id).values_list("pk", flat=True).first()
        if facility is None:
            raise ValidationError("This card has no credit facility.")
        account, credit = _lock_credit(facility)
        card = BankCard.objects.select_for_update().get(pk=card.pk)

        existing = CreditTransaction.objects.filter(reference=reference).first()
        if existing:
            if (existing.credit_account_id, existing.transaction_type, existing.amount) == (credit.pk, "purchase", amount):
                return existing
            raise ValidationError("Duplicate reference.")

        today = timezone.localdate()
        if card.card_type != "credit":
            raise ValidationError("This is not a credit card.")
        if card.status != "active":
            raise ValidationError("This card is not active.")
        if (card.expiry_year, card.expiry_month) < (today.year, today.month):
            raise ValidationError("This card has expired.")
        if account.closed_at:
            raise ValidationError("This account is closed.")
        if credit.status != "active":
            raise ValidationError("Your credit facility is not active.")
        if amount > credit.available_credit:
            raise ValidationError("Insufficient available credit.")

        credit.outstanding_balance += amount
        credit.save(update_fields=["outstanding_balance"])
        return CreditTransaction.objects.create(
            credit_account=credit, transaction_type="purchase", amount=amount,
            reference=reference, description=(merchant or "Card purchase")[:255])


def refund_credit_purchase(purchase_reference, amount=None):
    """One refund per purchase, full or partial. It lowers what is owed. If the purchase was
    already repaid, the extra goes back to the main balance."""
    if len(purchase_reference) > 99:
        raise ValidationError("Reference too long.")
    orig = CreditTransaction.objects.filter(reference=purchase_reference, transaction_type="purchase").first()
    if orig is None:
        raise ValidationError("Purchase not found.")
    ref = purchase_reference + "R"
    with transaction.atomic():
        account, credit = _lock_credit(orig.credit_account_id)
        existing = CreditTransaction.objects.filter(reference=ref).first()
        if existing:
            return existing
        amount = orig.amount if amount is None else _money(amount)
        if amount > orig.amount:
            raise ValidationError("The refund is more than the purchase.")
        cut = min(amount, credit.outstanding_balance)
        extra = amount - cut
        credit.outstanding_balance -= cut
        credit.save(update_fields=["outstanding_balance"])
        if extra > 0:
            account.balance += extra
            account.save(update_fields=["balance"])
            Transaction.objects.create(account=account, transaction_type="deposit", amount=extra,
                                       reference="KLF" + uuid4().hex[:12].upper(),
                                       description=f"Refund: {orig.description}"[:255])
        return CreditTransaction.objects.create(
            credit_account=credit, transaction_type="refund", amount=amount, reference=ref,
            description=f"Refund: {orig.description}"[:255])


def repay_credit(credit_account, amount, *, reference=None, pin=None):
    """Repay from the main balance. Pass pin to have it checked on the locked row. Both ledgers
    get a row with the same reference, and the same reference twice pays once."""
    amount = _money(amount)
    reference = reference or "KLR" + uuid4().hex[:16].upper()
    bad = result = None
    with transaction.atomic():
        account, credit = _lock_credit(credit_account.pk)
        existing = CreditTransaction.objects.filter(reference=reference).first()
        if existing:
            if (existing.credit_account_id, existing.transaction_type, existing.amount) == (credit.pk, "repayment", amount):
                return existing
            raise ValidationError("Duplicate reference.")
        if account.closed_at:
            raise ValidationError("This account is closed.")
        if credit.status not in ("active", "suspended"):       # a suspended customer can still repay
            raise ValidationError("Your credit facility is not open.")
        if pin is not None:
            if account.is_locked():
                raise ValidationError("Your account is temporarily locked.")
            if not account.pin_hash:
                raise ValidationError("Please set your transaction PIN first.")
            if not account.check_pin(str(pin)):                # saved on the locked row
                bad = "Your account is temporarily locked." if account.is_locked() else "Incorrect transaction PIN."
        if bad is None:
            if amount > credit.outstanding_balance:
                raise ValidationError("Repayment exceeds your outstanding balance.")
            if amount > account.balance:
                raise ValidationError("Insufficient account balance for repayment.")
            account.balance -= amount
            account.save(update_fields=["balance"])
            credit.outstanding_balance -= amount
            credit.save(update_fields=["outstanding_balance"])
            Transaction.objects.create(account=account, transaction_type="payment", amount=amount,
                                       reference=reference[:20], description="Credit card repayment")
            result = CreditTransaction.objects.create(
                credit_account=credit, transaction_type="repayment", amount=amount,
                reference=reference, description="Credit card repayment")
    if bad:
        raise ValidationError(bad)                             # raised after the commit, so the attempt is kept
    return result
    
    

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
    
    if CreditApplication.objects.filter(user=user, status__in=("pending", "assessed")).exists():
        raise ValidationError("You already have a credit request in progress.")

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

    check_eligibility(account)

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
    

    order, created = CardOrder.objects.get_or_create(
        account=account,
        card_type="credit",
        card_format="virtual",
        defaults={
            "cardholder_name": (
                account.user.get_full_name()
                or account.user.username
            ),
            "status": "pending",
        },
    )

    if created:
        issue_virtual_card_order(order.pk)


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
