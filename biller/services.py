import logging
import re
import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.module_loading import import_string

from accounts.models import Account
from accounts.services import limits_for
from accounts.utils import normalize_phone
from transactions.models import Transaction
from .models import BillPayment
from .rules import FEES, FIXED_PRICE, RULES

log = logging.getLogger(__name__)
LABELS = dict(BillPayment.CATEGORIES)


class PaymentError(Exception):
    pass


def get_provider():
    return import_string(settings.BILLER_PROVIDER)()


def new_reference():
    return "BP" + uuid.uuid4().hex[:10].upper()


def clean_amount(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise PaymentError("Enter a valid amount.")
    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
        raise PaymentError("Enter an amount above zero with at most 2 decimal places.")
    return amount


def clean_customer_ref(category, raw):
    raw = (raw or "").strip()
    if RULES[category]["ref"] == "phone":
        phone = normalize_phone(raw)
        if not phone:
            raise PaymentError("Enter a valid Nigerian phone number.")
        return phone
    if not re.fullmatch(r"[A-Za-z0-9\-]{5,30}", raw):
        raise PaymentError("Enter a valid number or ID.")
    return raw.upper()


def spent_today(account, category=None):
    qs = BillPayment.objects.filter(account=account, status__in=["processing", "completed"],
                                    created_at__date=timezone.localdate())
    if category:
        qs = qs.filter(category=category)
    return qs.aggregate(t=Sum("amount"))["t"] or Decimal("0")


def start_payment(account, *, category, provider_code, customer_ref, amount, reference, pin,
                  product_code=""):
    if category not in RULES:
        raise PaymentError("That service is not available.")
    rule = RULES[category]
    if rule["product"] and not product_code:
        raise PaymentError("Choose a product.")
    if not reference or len(reference) > 20:
        raise PaymentError("Missing reference.")
    if not str(pin or "").strip():
        raise PaymentError("Enter your transaction PIN.")

    cref = clean_customer_ref(category, customer_ref)
    if category in FIXED_PRICE:                          # the provider sets the price, not the browser
        price = get_provider().price_of(category, provider_code, product_code)
        if price is None:
            raise PaymentError("That product is not available.")
        amount = price
    amount = clean_amount(amount)
    if amount < rule["min"] or amount > rule["max"]:
        raise PaymentError(f"Amount must be between ₦{rule['min']:,.0f} and ₦{rule['max']:,.0f}.")
    fee = FEES.get(category, Decimal("0.00"))

    order, bad_pin = None, None
    try:
        with transaction.atomic():
            acc = Account.objects.select_for_update().get(pk=account.pk)

            existing = BillPayment.objects.filter(reference=reference).first()
            if existing:                                 # a retry: same request, no second charge
                same = (existing.account_id, existing.category, existing.customer_ref, existing.amount)
                if same != (acc.pk, category, cref, amount):
                    raise PaymentError("Duplicate reference.")
                order = existing
            else:
                if acc.closed_at:
                    raise PaymentError("Your account is closed.")
                if acc.is_locked():
                    raise PaymentError("Your account is temporarily locked.")
                if not acc.pin_hash:
                    raise PaymentError("Please set your transaction PIN first.")

                if not acc.check_pin(str(pin)):          # saves the attempt count on the locked row
                    bad_pin = "Your account is temporarily locked." if acc.is_locked() else "Incorrect transaction PIN."
                else:
                    if acc.kyc_tier < rule["tier"]:
                        raise PaymentError("Verify your ID to use this service.")
                    if spent_today(acc) + amount > limits_for(acc)["daily"]:
                        raise PaymentError("Daily limit reached.")
                    if "daily" in rule and spent_today(acc, category) + amount > rule["daily"]:
                        raise PaymentError(f"Daily limit for this service is ₦{rule['daily']:,.0f}.")
                    total = amount + fee
                    if acc.balance < total:
                        raise PaymentError("Insufficient balance.")

                    acc.balance -= total
                    acc.save(update_fields=["balance"])
                    order = BillPayment.objects.create(
                        account=acc, category=category, provider_code=provider_code,
                        product_code=product_code, customer_ref=cref, amount=amount, fee=fee,
                        reference=reference, status="processing")
                    Transaction.objects.create(
                        account=acc, transaction_type="payment", amount=total, reference=reference,
                        description=f"{LABELS[category]} · {provider_code.upper()} · ••••{cref[-4:]}"[:255])
    except IntegrityError:
        raise PaymentError("Duplicate reference.")

    if bad_pin:
        raise PaymentError(bad_pin)                      # raised after the commit, so the attempt is kept
    return settle(order.pk)


def settle(order_pk):
    """Call the provider once. Only one caller can claim an order, so a double tap can't buy twice."""
    claimed = BillPayment.objects.filter(
        pk=order_pk, status="processing", submitted_at__isnull=True
    ).update(submitted_at=timezone.now())
    order = BillPayment.objects.get(pk=order_pk)
    if not claimed:
        return order
    try:
        result = get_provider().purchase(order)          # outside every lock
    except Exception:
        log.exception("Provider error for %s. It stays 'processing' until it is checked.", order.reference)
        return order                                     # unknown outcome: never refund on a guess
    return apply_result(order.pk, result)


def _reverse(order, reason):
    acc = Account.objects.select_for_update().get(pk=order.account_id)
    total = order.amount + order.fee
    acc.balance += total
    acc.save(update_fields=["balance"])
    Transaction.objects.create(
        account=acc, transaction_type="deposit", amount=total, reference=order.reference + "R",
        description=f"Refund: {LABELS[order.category]} failed"[:255])
    order.status, order.failure_reason = "reversed", (reason or "")[:200]
    order.save(update_fields=["status", "failure_reason", "updated_at"])


def apply_result(order_pk, result):
    acc_id = BillPayment.objects.values_list("account_id", flat=True).get(pk=order_pk)
    with transaction.atomic():
        Account.objects.select_for_update().get(pk=acc_id)          # account first, then the order
        order = BillPayment.objects.select_for_update().get(pk=order_pk)
        if order.status != "processing":
            return order
        if result.status == "success":
            order.status, order.completed_at = "completed", timezone.now()
            order.provider_reference, order.token = result.provider_reference, result.token
            order.save(update_fields=["status", "completed_at", "provider_reference", "token", "updated_at"])
        elif result.status == "failed":
            _reverse(order, result.message or "Provider declined")
        return order                                     # "pending": leave it for the reconcile job