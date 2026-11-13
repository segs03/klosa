
import uuid
from decimal import Decimal, InvalidOperation
from django.db import IntegrityError, transaction

from django.db.models import Sum
from django.utils import timezone

from accounts.models import Account
from accounts.services import limits_for
from transactions.models import Transaction
from .models import MoneyRequest, Transfer


class TransferError(Exception):
    pass


def _has(model, name):
    return any(f.name == name for f in model._meta.get_fields())


def new_reference():
    return "KL" + uuid.uuid4().hex[:10].upper()


def clean_amount(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise TransferError("Enter a valid amount.")
    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
        raise TransferError("Enter an amount above zero with at most 2 decimal places.")
    return amount


def sent_today(account):
    total = (Transfer.objects
             .filter(sender=account, status="completed", created_at__date=timezone.localdate())
             .aggregate(t=Sum("amount"))["t"])
    return total or Decimal("0")


def lock_accounts(*ids):
    """Always lock in id order, so two transfers crossing each other can't deadlock."""
    return {a.pk: a for a in
            Account.objects.select_for_update().filter(pk__in=set(ids)).order_by("pk")}


def _name(account):
    return account.user.get_full_name().strip() or "Klosa customer"


def move_money(sender, receiver, amount, *, note="", beneficiary=None, reference=None):
    """Both accounts must already be locked, inside transaction.atomic()."""
    if sender.pk == receiver.pk:
        raise TransferError("You can't send money to your own account.")
    if sender.closed_at:
        raise TransferError("Your account is closed.")
    if receiver.closed_at:
        raise TransferError("This account is not available.")

    lim = limits_for(sender)
    if amount > lim["single"]:
        raise TransferError(f"Your limit is ₦{lim['single']:,.0f} per transfer. Verify your ID to raise it.")
    if sent_today(sender) + amount > lim["daily"]:
        raise TransferError("Daily limit reached.")
    if sender.balance < amount:
        raise TransferError("Insufficient balance.")

    ref = reference or new_reference()
    sender.balance -= amount
    receiver.balance += amount
    sender.save(update_fields=["balance"])
    receiver.save(update_fields=["balance"])

    transfer = Transfer.objects.create(
        sender=sender, receiver=receiver, amount=amount,
        reference=ref, note=note[:255], status="completed")

    to_name, from_name = _name(receiver), _name(sender)
    out = dict(account=sender, transaction_type="transfer", amount=amount, reference=ref,
               description=(f"Transfer to {to_name}" + (f": {note}" if note else ""))[:255],
               beneficiary=beneficiary)
    inc = dict(account=receiver, transaction_type="deposit", amount=amount, reference=ref,
               description=f"Transfer from {from_name}"[:255])
    if _has(Transaction, "counterparty_name"):
        out.update(counterparty_name=to_name, counterparty_account=receiver.account_number)
        inc.update(counterparty_name=from_name, counterparty_account=sender.account_number)
    Transaction.objects.create(**out)
    Transaction.objects.create(**inc)
    return transfer


def internal_transfer(sender_id, receiver_id, amount, **kw):
    """Klosa-to-Klosa transfer. The PIN must be checked by the view before calling this."""
    amount = clean_amount(amount)
    with transaction.atomic():
        rows = lock_accounts(sender_id, receiver_id)
        if sender_id not in rows or receiver_id not in rows:
            raise TransferError("This account is not available.")
        return move_money(rows[sender_id], rows[receiver_id], amount, **kw)


def pay_money_request(request_pk, payer_user):
    first = MoneyRequest.objects.filter(pk=request_pk, recipient=payer_user).first()
    if not first:
        raise TransferError("Request not found.")
    payer = Account.objects.filter(user_id=first.recipient_id).first()
    asker = Account.objects.filter(user_id=first.requester_id).first()
    if not payer or not asker:
        raise TransferError("This request can't be paid.")

    expired, transfer = False, None
    with transaction.atomic():
        lock_accounts(payer.pk, asker.pk)                       # accounts first, then the request
        req = MoneyRequest.objects.select_for_update().get(pk=request_pk)
        if req.recipient_id != payer_user.pk or req.status != "pending":
            raise TransferError("This request is no longer open.")
        if req.is_expired:
            req.status = "expired"
            req.save(update_fields=["status"])
            expired = True
        else:
            rows = lock_accounts(payer.pk, asker.pk)
            transfer = move_money(rows[payer.pk], rows[asker.pk], req.amount, note=req.note)
            req.status, req.paid_at, req.transfer = "paid", timezone.now(), transfer
            req.save(update_fields=["status", "paid_at", "transfer"])
    if expired:                                                  # raised after the commit, so "expired" is kept
        raise TransferError("This request has expired.")
    return transfer


def send_money(sender, receiver, amount, reference=None, pin=None, *, note="", beneficiary=None):
    """Klosa-to-Klosa transfer with a PIN check.

    A wrong PIN is recorded and committed, then reported. Money only moves after the PIN is
    confirmed on the locked sender row. Passing a `reference` makes retries safe: sending the
    same reference twice returns the first transfer and moves no money.
    """
    amount = clean_amount(amount)
    if pin is None or not str(pin).strip():
        raise TransferError("Enter your transaction PIN.")
    ref = reference or new_reference()

    bad_pin = None
    try:
        with transaction.atomic():
            rows = lock_accounts(sender.pk, receiver.pk)
            if sender.pk not in rows or receiver.pk not in rows:
                raise TransferError("Account not found.")
            s, r = rows[sender.pk], rows[receiver.pk]

            existing = Transfer.objects.filter(reference=ref).first()
            if existing:
                if existing.sender_id == s.pk and existing.receiver_id == r.pk and existing.amount == amount:
                    return existing                       # same request sent again: no second payment
                raise TransferError("Duplicate transfer reference.")

            if s.currency != r.currency:
                raise TransferError("Account currencies do not match.")
            if s.is_locked():
                raise TransferError("Your account is temporarily locked.")
            if not s.pin_hash:
                raise TransferError("Please set your transaction PIN first.")

            if not s.check_pin(str(pin)):                 # saves the attempt count, runs on the locked row
                bad_pin = "Your account is temporarily locked." if s.is_locked() else "Incorrect transaction PIN."
            else:
                return move_money(s, r, amount, note=note, beneficiary=beneficiary, reference=ref)
    except IntegrityError:
        # two requests with the same reference arrived at once; the database let only one in
        raise TransferError("Duplicate transfer reference.")

    raise TransferError(bad_pin)                          # raised after the commit, so the attempt is kept