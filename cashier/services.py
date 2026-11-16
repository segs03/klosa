import hashlib
import hmac
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
from beneficiary.banks import KLOSA, bank_code_for
from transactions.models import Transaction
from . import rules
from .models import Deposit, Withdrawal

log = logging.getLogger(__name__)


class CashierError(Exception):
    pass


def get_provider():
    return import_string(settings.CASHIER_PROVIDER)()


def new_ref(prefix):
    return prefix + uuid.uuid4().hex[:10].upper()


def clean_amount(value, lo, hi):
    try:
        amount = Decimal(str(value).strip())
        ok = amount.is_finite() and amount == amount.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        ok = False
    if not ok:
        raise CashierError("Enter a valid amount with at most 2 decimal places.")
    if amount < lo or amount > hi:
        raise CashierError(f"Amount must be between ₦{lo:,.0f} and ₦{hi:,.0f}.")
    return amount


def signature_ok(body, header):
    secret = getattr(settings, "CASHIER_WEBHOOK_SECRET", "")
    if not secret or not header:                       # no secret set: reject everything
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.strip().lower())


# ------------------------------------------------------------------ deposits
def start_deposit(account, amount):
    amount = clean_amount(amount, rules.DEPOSIT_MIN, rules.DEPOSIT_MAX)
    if account.closed_at:
        raise CashierError("Your account is closed.")
    dep = Deposit.objects.create(account=account, amount=amount, reference=new_ref("DP"))
    try:
        url, pref = get_provider().start_checkout(dep)
    except Exception:
        log.exception("Could not start checkout for %s", dep.reference)
        Deposit.objects.filter(pk=dep.pk).update(status="failed", note="Could not start checkout")
        raise CashierError("We couldn't start the payment. Try again.")
    dep.checkout_url, dep.provider_reference = url, pref
    dep.save(update_fields=["checkout_url", "provider_reference"])
    return dep


def credit_deposit(reference, paid_amount, provider_reference=""):
    """Idempotent. Credits only when the amount paid matches what was asked for."""
    acc_id = Deposit.objects.filter(reference=reference).values_list("account_id", flat=True).first()
    if acc_id is None:
        return None
    try:
        paid = Decimal(str(paid_amount))
        paid = paid if paid.is_finite() and paid > 0 else None
    except (InvalidOperation, ValueError):
        paid = None
    with transaction.atomic():
        acc = Account.objects.select_for_update().get(pk=acc_id)        # account first, then the deposit
        dep = Deposit.objects.select_for_update().get(reference=reference)
        if dep.status not in ("pending", "expired"):
            return dep                                                   # already handled: do nothing
        problem = None
        if paid is None or paid != dep.amount:
            problem = f"Provider reported {paid_amount}, expected {dep.amount}"
        elif acc.closed_at:
            problem = "Account is closed"
        if problem:
            dep.status, dep.note = "review", problem[:200]
            dep.save(update_fields=["status", "note"])
            return dep
        acc.balance += paid
        acc.save(update_fields=["balance"])
        dep.status, dep.paid_amount, dep.completed_at = "completed", paid, timezone.now()
        if provider_reference and not dep.provider_reference:
            dep.provider_reference = provider_reference
        dep.save(update_fields=["status", "paid_amount", "completed_at", "provider_reference"])
        Transaction.objects.create(account=acc, transaction_type="deposit", amount=paid,
                                   reference=dep.reference, description="Deposit via card")
    return dep


def credit_inbound_transfer(account_number, amount, provider_reference, sender=""):
    """A transfer from another bank into this customer's Klosa account number. Credited once."""
    amount = clean_amount(amount, Decimal("0.01"), Decimal("1000000000"))
    if not provider_reference:
        raise CashierError("Missing provider reference.")
    acc_id = Account.objects.filter(account_number=account_number).values_list("pk", flat=True).first()
    if acc_id is None:
        return None
    try:
        with transaction.atomic():
            acc = Account.objects.select_for_update().get(pk=acc_id)
            existing = Deposit.objects.filter(provider_reference=provider_reference).first()
            if existing:
                return existing
            closed = bool(acc.closed_at)
            dep = Deposit.objects.create(
                account=acc, method="transfer", amount=amount, paid_amount=None if closed else amount,
                reference=new_ref("DT"), provider_reference=provider_reference,
                status="review" if closed else "completed", note="Account is closed" if closed else "",
                completed_at=None if closed else timezone.now())
            if not closed:
                acc.balance += amount
                acc.save(update_fields=["balance"])
                Transaction.objects.create(
                    account=acc, transaction_type="deposit", amount=amount, reference=dep.reference,
                    description=(f"Transfer from {sender}" if sender else "Bank transfer received")[:255])
            return dep
    except IntegrityError:                              # the same message arrived twice at once
        return Deposit.objects.filter(provider_reference=provider_reference).first()


# --------------------------------------------------------------- withdrawals
def withdrawn_today(account):
    total = (Withdrawal.objects
             .filter(account=account, status__in=["processing", "completed"],
                     created_at__date=timezone.localdate())
             .aggregate(t=Sum("amount"))["t"])
    return total or Decimal("0")


def start_withdrawal(account, *, bank_name, account_number, account_name, amount, reference, pin):
    code = bank_code_for(bank_name)
    if bank_name == KLOSA or not code:
        raise CashierError("Choose a bank from the list.")
    if not re.fullmatch(r"\d{10}", account_number or ""):
        raise CashierError("Enter a valid 10-digit account number.")
    if not (account_name or "").strip():
        raise CashierError("Confirm the account name first.")
    if not reference or len(reference) > 20:
        raise CashierError("Missing reference.")
    if not str(pin or "").strip():
        raise CashierError("Enter your transaction PIN.")
    amount = clean_amount(amount, rules.WITHDRAW_MIN, rules.WITHDRAW_MAX)
    fee = rules.WITHDRAW_FEE
    min_tier = getattr(settings, "CASHIER_WITHDRAW_MIN_TIER", 1)

    order, bad_pin = None, None
    try:
        with transaction.atomic():
            acc = Account.objects.select_for_update().get(pk=account.pk)
            existing = Withdrawal.objects.filter(reference=reference).first()
            if existing:                                 # a retry: same request, no second charge
                same = (existing.account_id, existing.bank_code, existing.account_number, existing.amount)
                if same != (acc.pk, code, account_number, amount):
                    raise CashierError("Duplicate reference.")
                order = existing
            else:
                if acc.closed_at:
                    raise CashierError("Your account is closed.")
                if acc.is_locked():
                    raise CashierError("Your account is temporarily locked.")
                if not acc.pin_hash:
                    raise CashierError("Please set your transaction PIN first.")
                if not acc.check_pin(str(pin)):          # saved on the locked row, so the 3-try lock works
                    bad_pin = "Your account is temporarily locked." if acc.is_locked() else "Incorrect transaction PIN."
                else:
                    if acc.kyc_tier < min_tier:
                        raise CashierError("Verify your ID to withdraw money.")
                    lim = limits_for(acc)
                    if amount > lim["single"]:
                        raise CashierError(f"Your limit is ₦{lim['single']:,.0f} per withdrawal. Verify your ID to raise it.")
                    if withdrawn_today(acc) + amount > lim["daily"]:
                        raise CashierError("Daily limit reached.")
                    total = amount + fee
                    if acc.balance < total:
                        raise CashierError("Insufficient balance.")
                    acc.balance -= total
                    acc.save(update_fields=["balance"])
                    order = Withdrawal.objects.create(
                        account=acc, bank_name=bank_name, bank_code=code, account_number=account_number,
                        account_name=account_name.strip()[:100], amount=amount, fee=fee, reference=reference)
                    Transaction.objects.create(
                        account=acc, transaction_type="withdrawal", amount=total, reference=reference,
                        description=f"Withdrawal · {bank_name} ••••{account_number[-4:]}"[:255])
    except IntegrityError:
        raise CashierError("Duplicate reference.")
    if bad_pin:
        raise CashierError(bad_pin)                      # raised after the commit, so the attempt is kept
    return settle_withdrawal(order.pk)


def settle_withdrawal(pk):
    """Call the provider once. Only one caller can claim an order, so a double tap can't pay twice."""
    claimed = Withdrawal.objects.filter(pk=pk, status="processing", submitted_at__isnull=True
                                        ).update(submitted_at=timezone.now())
    order = Withdrawal.objects.get(pk=pk)
    if not claimed:
        return order
    try:
        result = get_provider().payout(order)            # outside every lock
    except Exception:
        log.exception("Payout error for %s. It stays 'processing' until it is checked.", order.reference)
        return order                                     # unknown outcome: never refund on a guess
    return apply_payout(order.pk, result)


def _reverse_withdrawal(w, reason):
    acc = Account.objects.select_for_update().get(pk=w.account_id)
    total = w.amount + w.fee
    acc.balance += total
    acc.save(update_fields=["balance"])
    Transaction.objects.create(account=acc, transaction_type="deposit", amount=total,
                               reference=w.reference + "R", description="Refund: Withdrawal failed")
    w.status, w.failure_reason = "reversed", (reason or "")[:200]
    w.save(update_fields=["status", "failure_reason", "updated_at"])


def apply_payout(pk, result):
    acc_id = Withdrawal.objects.values_list("account_id", flat=True).get(pk=pk)
    with transaction.atomic():
        Account.objects.select_for_update().get(pk=acc_id)               # account first, then the order
        w = Withdrawal.objects.select_for_update().get(pk=pk)
        if w.status != "processing":
            return w
        if result.status == "success":
            w.status, w.completed_at = "completed", timezone.now()
            w.provider_reference = result.provider_reference or w.provider_reference
            w.save(update_fields=["status", "completed_at", "provider_reference", "updated_at"])
        elif result.status == "failed":
            _reverse_withdrawal(w, result.message or "Bank declined")
        return w                                         # "pending": leave it for the reconcile job