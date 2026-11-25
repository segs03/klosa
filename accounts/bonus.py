import logging
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from transactions.models import Transaction
from .credit_services import _get_credit_policy, eligibility_problem
from .kyc_services import verified_types
from .models import Account, WelcomeBonus
from .tiers import has_required_ids

log = logging.getLogger(__name__)


def bonus_amount():
    if not getattr(settings, "WELCOME_BONUS_ENABLED", False):
        return None
    return Decimal(str(getattr(settings, "WELCOME_BONUS_AMOUNT", "200000")))


def credit_offer(account):
    """(eligible, text) for the credit notice."""
    min_age = int(getattr(settings, "CREDIT_MIN_ACCOUNT_AGE_DAYS", 30))
    left = min_age - (timezone.now() - account.created_at).days
    try:
        upto = f" up to ₦{_get_credit_policy(account).maximum_limit:,.0f}"
    except ValidationError:
        upto = ""
    problem = eligibility_problem(account)
    if problem is None:
        return True, f"You can now request credit{upto}. It only takes a minute."
    if left > 0:
        return False, f"You will be able to request credit{upto} in {left} day(s)."
    return False, problem


def send_credit_offer(user, account, key="credit_offer_ready"):
    from notices.services import notify_user
    eligible, text = credit_offer(account)
    return notify_user(user, "You qualify for credit" if eligible else "Credit is coming soon", text,
                       "success" if eligible else "info",
                       reverse("accounts:credit_request") if eligible else reverse("accounts:credit"),
                       key=key if eligible else "credit_offer")


def grant_welcome_bonus(user):
    """Pays once, only when the BVN and one more ID (NIN, passport or driver's licence) are both
    verified, and only while the budget lasts. Call it inside the transaction that verified the ID."""
    amount = bonus_amount()
    if amount is None or amount <= 0 or not has_required_ids(verified_types(user)):
        return None
    budget = Decimal(str(getattr(settings, "WELCOME_BONUS_BUDGET", "0")))
    try:
        with transaction.atomic():
            acc = Account.objects.select_for_update().filter(user=user).first()
            if acc is None or acc.closed_at or WelcomeBonus.objects.filter(user=user).exists():
                return None
            given = WelcomeBonus.objects.aggregate(t=Sum("amount"))["t"] or Decimal("0")
            if given + amount > budget:
                log.warning("Welcome bonus budget is used up. User %s was not paid.", user.pk)
                return None
            ref = "WB" + uuid.uuid4().hex[:10].upper()
            bonus = WelcomeBonus.objects.create(user=user, amount=amount, reference=ref)
            acc.balance += amount
            acc.save(update_fields=["balance"])
            Transaction.objects.create(account=acc, transaction_type="deposit", amount=amount,
                                       reference=ref, description="Welcome bonus")
            send_credit_offer(user, acc)
            return bonus
    except IntegrityError:
        return None
