from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
import secrets

from .models import Account, CreditAccount
from cards.models import BankCard

from uuid import uuid4
from django.core.exceptions import ValidationError

from transactions.models import CreditTransaction


# Demo approval policy
STARTER_LIMIT = Decimal("250000.00")
STANDARD_LIMIT = Decimal("500000.00")


@transaction.atomic
def approve_credit(account):
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

    credit.credit_limit = Decimal("50000.00")
    credit.status = "active"
    credit.approved_at = timezone.now()
    credit.save()

    # Create a separate credit card
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
        }
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