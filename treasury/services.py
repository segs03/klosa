import logging
import secrets
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from accounts.models import Account
from accounts.services import limits_for
from accounts.utils import digest, hit
from transactions.models import Transaction
from . import rules
from .models import CashOut, CashRequest

log = logging.getLogger(__name__)
ZERO = Decimal("0.00")
CREDITS = ("deposit", "savings_withdrawal")
DEBITS = ("withdrawal", "payment", "transfer", "savings_deposit")


class TreasuryError(Exception):
    pass


def _need(user, perm):
    if not (user and user.is_authenticated and user.is_active and user.is_staff and user.has_perm(perm)):
        raise PermissionDenied("You are not allowed to do this.")


def _amount(value, lo, hi):
    try:
        amount = Decimal(str(value).strip())
        ok = amount.is_finite() and amount == amount.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        ok = False
    if not ok:
        raise TreasuryError("Enter a valid amount with at most 2 decimal places.")
    if amount < lo or amount > hi:
        raise TreasuryError(f"Amount must be between ₦{lo:,.0f} and ₦{hi:,.0f}.")
    return amount


# ---------------------------------------------------------------- cash in
def create_cash_in(maker, account_number, amount, branch, note=""):
    _need(maker, "treasury.record_cash")
    amount = _amount(amount, rules.CASHIN_MIN, rules.CASHIN_MAX)
    branch = " ".join((branch or "").split())[:60]
    if not branch:
        raise TreasuryError("Enter the branch or agent name.")
    acc = Account.objects.filter(account_number=(account_number or "").strip(), closed_at__isnull=True).first()
    if acc is None:
        raise TreasuryError("No open account has this number.")
    if acc.user_id == maker.pk:
        raise TreasuryError("You cannot record cash for your own account.")
    return CashRequest.objects.create(
        account=acc, amount=amount, branch=branch, note=(note or "")[:200],
        reference="CI" + uuid.uuid4().hex[:10].upper(), maker=maker)


def approve_cash_in(request_pk, checker):
    _need(checker, "treasury.approve_cash")
    acc_id = CashRequest.objects.filter(pk=request_pk).values_list("account_id", flat=True).first()
    if acc_id is None:
        raise TreasuryError("Request not found.")
    with transaction.atomic():
        acc = Account.objects.select_for_update().get(pk=acc_id)            # account first, then the request
        req = CashRequest.objects.select_for_update().get(pk=request_pk)
        if req.status != "pending":
            raise TreasuryError("This request was already handled.")
        if req.maker_id == checker.pk:
            raise PermissionDenied("The person who recorded the cash cannot approve it.")
        if acc.user_id == checker.pk:
            raise PermissionDenied("You cannot approve cash for your own account.")
        now = timezone.now()
        req.checker, req.decided_at = checker, now
        if acc.closed_at:
            req.status, req.reason = "rejected", "Account is closed"
            req.save(update_fields=["status", "checker", "decided_at", "reason"])
            return req
        acc.balance += req.amount
        acc.save(update_fields=["balance"])
        Transaction.objects.create(account=acc, transaction_type="deposit", amount=req.amount,
                                   reference=req.reference, description=f"Cash deposit · {req.branch}"[:255])
        req.status = "posted"
        req.save(update_fields=["status", "checker", "decided_at"])
        return req


def reject_cash_in(request_pk, checker, reason):
    _need(checker, "treasury.approve_cash")
    reason = (reason or "").strip()
    if not reason:
        raise TreasuryError("Give a reason.")
    with transaction.atomic():
        req = CashRequest.objects.select_for_update().get(pk=request_pk)
        if req.status != "pending":
            raise TreasuryError("This request was already handled.")
        if req.maker_id == checker.pk:
            raise PermissionDenied("Ask a colleague to decide this one.")
        req.status, req.checker, req.reason, req.decided_at = "rejected", checker, reason[:200], timezone.now()
        req.save(update_fields=["status", "checker", "reason", "decided_at"])
        return req


# --------------------------------------------------------------- cash out
def _code_hash(account_pk, code):
    return digest("cashout", account_pk, code)


def request_cash_out(account, *, amount, reference, pin):
    """Takes the money now and returns (cash_out, code). The code is shown once. A retry with the
    same reference returns (cash_out, None) and takes nothing more."""
    amount = _amount(amount, rules.CASHOUT_MIN, rules.CASHOUT_MAX)
    if not reference or len(reference) > 19:
        raise TreasuryError("Missing reference.")
    if not str(pin or "").strip():
        raise TreasuryError("Enter your transaction PIN.")
    result = code = bad = None
    try:
        with transaction.atomic():
            acc = Account.objects.select_for_update().get(pk=account.pk)
            existing = CashOut.objects.filter(reference=reference).first()
            if existing:
                if (existing.account_id, existing.amount) == (acc.pk, amount):
                    return existing, None
                raise TreasuryError("Duplicate reference.")
            if acc.closed_at:
                raise TreasuryError("Your account is closed.")
            if acc.is_locked():
                raise TreasuryError("Your account is temporarily locked.")
            if not acc.pin_hash:
                raise TreasuryError("Please set your transaction PIN first.")
            if not acc.check_pin(str(pin)):                    # saved on the locked row, so the 3-try lock works
                bad = "Your account is temporarily locked." if acc.is_locked() else "Incorrect transaction PIN."
            else:
                if acc.kyc_tier < rules.MIN_TIER:
                    raise TreasuryError("Verify your ID to withdraw cash.")
                lim = limits_for(acc)
                if amount > lim["single"]:
                    raise TreasuryError(f"Your limit is ₦{lim['single']:,.0f} per withdrawal. Verify your ID to raise it.")
                today = (CashOut.objects.filter(account=acc, status__in=["held", "paid"],
                                                created_at__date=timezone.localdate())
                         .aggregate(t=Sum("amount"))["t"] or ZERO)
                if today + amount > lim["daily"]:
                    raise TreasuryError("Daily limit reached.")
                if acc.balance < amount:
                    raise TreasuryError("Insufficient balance.")
                acc.balance -= amount
                acc.save(update_fields=["balance"])
                code = f"{secrets.randbelow(10**6):06d}"
                result = CashOut.objects.create(
                    account=acc, amount=amount, reference=reference, code_hash=_code_hash(acc.pk, code),
                    expires_at=timezone.now() + timedelta(hours=rules.CASHOUT_HOURS))
                Transaction.objects.create(account=acc, transaction_type="withdrawal", amount=amount,
                                           reference=reference, description="Cash withdrawal at a branch")
    except IntegrityError:
        raise TreasuryError("Duplicate reference.")
    if bad:
        raise TreasuryError(bad)                               # raised after the commit, so the attempt is kept
    return result, code


def pay_cash_out(teller, account_number, code):
    _need(teller, "treasury.pay_cashout")
    if hit(f"co_teller:{teller.pk}", 40, 900):
        raise TreasuryError("Too many attempts. Wait a few minutes.")
    acc = Account.objects.filter(account_number=(account_number or "").strip()).first()
    if acc is None or hit(f"co_try:{acc.pk}", 5, 900):         # 5 guesses per account per 15 minutes
        raise TreasuryError("That code is not valid or has expired.")
    with transaction.atomic():
        co = (CashOut.objects.select_for_update()
              .filter(account=acc, status="held", expires_at__gt=timezone.now(),
                      code_hash=_code_hash(acc.pk, (code or "").strip()))
              .first())
        if co is None:
            raise TreasuryError("That code is not valid or has expired.")
        co.status, co.paid_by, co.paid_at = "paid", teller, timezone.now()
        co.save(update_fields=["status", "paid_by", "paid_at"])
        return co


def expire_cash_outs():
    count = 0
    due = list(CashOut.objects.filter(status="held", expires_at__lte=timezone.now()).values_list("pk", "account_id"))
    for pk, acc_id in due:
        with transaction.atomic():
            acc = Account.objects.select_for_update().get(pk=acc_id)
            co = CashOut.objects.select_for_update().get(pk=pk)
            if co.status != "held":
                continue
            acc.balance += co.amount
            acc.save(update_fields=["balance"])
            Transaction.objects.create(account=acc, transaction_type="deposit", amount=co.amount,
                                       reference=co.reference + "R", description="Refund: Cash withdrawal expired")
            co.status = "expired"
            co.save(update_fields=["status"])
            count += 1
    return count


# ------------------------------------------------------------------ audit
def audit_balances():
    """Every balance must equal the sum of its recorded transactions. Returns the accounts that do not."""
    rows = Account.objects.annotate(
        cr=Coalesce(Sum("transactions__amount", filter=Q(transactions__transaction_type__in=CREDITS)), ZERO),
        dr=Coalesce(Sum("transactions__amount", filter=Q(transactions__transaction_type__in=DEBITS)), ZERO))
    return [(a.account_number, a.balance, a.cr - a.dr) for a in rows if a.balance != a.cr - a.dr]