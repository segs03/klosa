"""Pay another Klosa user with a debit or credit card.

Debit card:  moves money from the sender's balance (same rules as a normal transfer).
Credit card: draws on the sender's credit line through accounts.credit_services.spend_on_credit,
             so credit rules live in one place. The receiver is credited, the sender's balance
             is untouched.

Lock order everywhere: accounts (by id) -> credit facility -> card.
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.credit_services import spend_on_credit
from accounts.services import limits_for
from cards.models import BankCard                         # ASSUMPTION: app label
from transactions.models import Transaction

from .models import Transfer
from .services import (                                    # ASSUMPTION: your transfer service module
    TransferError, _has, _name, clean_amount, lock_accounts, move_money, new_reference, sent_today,
)

CREDIT_MIN_TIER = 2


def _credit_caps():
    """Lower than debit on purpose: a credit-funded transfer is a cash advance."""
    single = Decimal(str(getattr(settings, "CREDIT_P2P_SINGLE", "50000")))
    daily = Decimal(str(getattr(settings, "CREDIT_P2P_DAILY", "100000")))
    return single, daily


def _credit_sent_today(account):
    total = (Transfer.objects
             .filter(sender=account, status="completed", funding_source="credit_card",
                     created_at__date=timezone.localdate())
             .aggregate(t=Sum("amount"))["t"])
    return total or Decimal("0")


def _check_card_usable(card):
    if card.status != "active":
        raise TransferError("This card is not active.")
    year = card.expiry_year + 2000 if card.expiry_year < 100 else card.expiry_year
    today = timezone.localdate()
    if (year, card.expiry_month) < (today.year, today.month):   # valid through the end of its month
        raise TransferError("This card has expired.")


def _tag(transfer, source, card):
    transfer.funding_source, transfer.card = source, card
    transfer.save(update_fields=["funding_source", "card"])
    return transfer


def _pay_debit(s, r, card_pk, amount, ref, note, beneficiary=None):
    card = BankCard.objects.select_for_update().get(pk=card_pk)   # lock, then check again
    _check_card_usable(card)
    transfer = move_money(s, r, amount, note=note, reference=ref, beneficiary=beneficiary,
                          via=f"Debit card •••• {card.last_four}")
    return _tag(transfer, "debit_card", card)


def _pay_credit(s, r, card, amount, ref, note, beneficiary=None):
    if s.closed_at:
        raise TransferError("Your account is closed.")
    if r.closed_at:
        raise TransferError("This account is not available.")
    if s.tier < CREDIT_MIN_TIER:
        raise TransferError("Verify your ID to pay with a credit card.")

    lim = limits_for(s)
    single_cap, daily_cap = _credit_caps()
    per_payment = min(lim["single"], single_cap)
    if amount > per_payment:
        raise TransferError(f"Credit card payments are limited to ₦{per_payment:,.0f} each.")
    if sent_today(s) + amount > lim["daily"]:
        raise TransferError("Daily limit reached.")
    if _credit_sent_today(s) + amount > daily_cap:
        raise TransferError("Daily credit card limit reached.")

    to_name, from_name = _name(r), _name(s)
    try:
        # Locks the credit facility, then the card, and checks card status, expiry, facility
        # status and available credit. The same reference twice spends once.
        spend_on_credit(card, amount, reference=ref, merchant=f"Transfer to {to_name}")
    except ValidationError as e:
        raise TransferError(e.messages[0])

    r.balance += amount
    r.save(update_fields=["balance"])                      # sender's own balance is untouched

    transfer = Transfer.objects.create(
        sender=s, receiver=r, amount=amount, reference=ref, note=note[:255], status="completed",
        funding_source="credit_card", card=card)

    out = dict(account=s, transaction_type="transfer", amount=amount, reference=ref,
               description=(f"Transfer to {to_name} (Credit card •••• {card.last_four})"
                            + (f": {note}" if note else ""))[:255],
               beneficiary=beneficiary)
    inc = dict(account=r, transaction_type="deposit", amount=amount, reference=ref,
               description=f"Transfer from {from_name}"[:255])
    if _has(Transaction, "counterparty_name"):
        out.update(counterparty_name=to_name, counterparty_account=r.account_number)
        inc.update(counterparty_name=from_name, counterparty_account=s.account_number)
    Transaction.objects.create(**out)
    Transaction.objects.create(**inc)
    return transfer


def pay_with_card(sender, receiver, card_id, amount, pin, reference=None, *, note="", beneficiary=None):
    """Pay a Klosa user with one of the sender's own cards.

    Same safety rules as send_money: a wrong PIN is recorded and committed, then reported;
    money moves only after the PIN is confirmed on the locked sender row; reusing a
    `reference` returns the first transfer instead of paying twice.
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
            if s.pk == r.pk:
                raise TransferError("You can't send money to your own account.")

            existing = Transfer.objects.filter(reference=ref).first()
            if existing:
                if (existing.sender_id == s.pk and existing.receiver_id == r.pk
                        and existing.amount == amount and existing.card_id == card_id):
                    return existing
                raise TransferError("Duplicate transfer reference.")

            if s.currency != r.currency:
                raise TransferError("Account currencies do not match.")
            if s.is_locked():
                raise TransferError("Your account is temporarily locked.")
            if not s.pin_hash:
                raise TransferError("Please set your transaction PIN first.")
            if s.transfers_locked_until and s.transfers_locked_until > timezone.now():
                raise TransferError("Transfers are temporarily locked on your account.")

            # Early, unlocked look so the customer gets a clear message. The money paths
            # lock the card and check it again.
            card = BankCard.objects.filter(pk=card_id).first()
            if card is None or card.account_id != s.pk:    # same message either way: don't reveal other people's cards
                raise TransferError("This card can't be used.")
            _check_card_usable(card)

            if not s.check_pin(str(pin)):
                bad_pin = "Your account is temporarily locked." if s.is_locked() else "Incorrect transaction PIN."
            elif card.card_type == "debit":
                return _pay_debit(s, r, card.pk, amount, ref, note, beneficiary)
            elif card.card_type == "credit":
                return _pay_credit(s, r, card, amount, ref, note, beneficiary)
            else:
                raise TransferError("This card can't be used.")
    except IntegrityError:
        raise TransferError("Duplicate transfer reference.")

    raise TransferError(bad_pin)                           # raised after the commit, so the attempt is kept
