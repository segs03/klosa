
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.core.exceptions import ValidationError

from accounts.models import Account
from .models import Transfer
from transactions.models import Transaction


@transaction.atomic
def send_money(sender, receiver, amount, reference, pin):
    # Validate amount
    try:
        amount = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Enter a valid amount.")

    if not amount.is_finite() or amount <= 0:
        raise ValidationError("Amount must be greater than zero.")

    if amount != amount.quantize(Decimal("0.01")):
        raise ValidationError("Amount cannot have more than 2 decimal places.")

    if not reference:
        raise ValidationError("Transfer reference is required.")

    if sender.pk == receiver.pk:
        raise ValidationError("You cannot transfer to yourself.")

    if sender.currency != receiver.currency:
        raise ValidationError("Account currencies do not match.")

    # Lock both accounts in a consistent order
    account_ids = sorted([sender.pk, receiver.pk])

    locked_accounts = {
        account.pk: account
        for account in Account.objects.select_for_update()
        .filter(pk__in=account_ids)
        .order_by("pk")
    }

    sender = locked_accounts.get(sender.pk)
    receiver = locked_accounts.get(receiver.pk)

    if not sender or not receiver:
        raise ValidationError("Account not found.")

    # PIN validation
    if sender.is_locked():
        raise ValidationError("Your account is temporarily locked.")

    if not sender.pin_hash:
        raise ValidationError("Please set your transaction PIN first.")

    # Important: commit failed PIN attempts before raising the error.
    if not sender.check_pin(pin):
        pin_error = True
    else:
        pin_error = False

    if pin_error:
        # Exit atomic block normally so failed PIN attempts are saved.
        pass
    else:
        if sender.balance < amount:
            raise ValidationError("Insufficient balance.")

        if Transfer.objects.filter(reference=reference).exists():
            raise ValidationError("Duplicate transfer reference.")

        # Update balances
        sender.balance -= amount
        receiver.balance += amount

        sender.save(update_fields=["balance"])
        receiver.save(update_fields=["balance"])

        # Create completed transfer
        transfer = Transfer.objects.create(
            sender=sender,
            receiver=receiver,
            amount=amount,
            reference=reference,
            status="completed"
        )

        # Sender transaction
        Transaction.objects.create(
            account=sender,
            transaction_type="transfer",
            amount=amount,
            description=f"Transfer to {receiver.account_number}"
        )

        # Receiver transaction
        Transaction.objects.create(
            account=receiver,
            transaction_type="deposit",
            amount=amount,
            description=f"Transfer from {sender.account_number}"
        )

    if pin_error:
        raise ValidationError("Incorrect transaction PIN.")

    return transfer
