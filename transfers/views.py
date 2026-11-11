import uuid

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError

from accounts.models import Account
from .forms import TransferForm, RequestForm, DAILY_LIMIT
from .services import send_money
from transactions.models import  Transaction
from beneficiary.models import Beneficiary

from decimal import Decimal
from django.db import transaction as db
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .models import Transfer, MoneyRequest




class SendFailed(Exception):
    pass


def sent_today(account):
    total = Transaction.objects.filter(
        account=account, transaction_type="transfer",
        created_at__date=timezone.localdate(),
    ).aggregate(t=Sum("amount"))["t"]
    return total or Decimal("0")


@login_required
def send_money_view(request):
    account = get_object_or_404(Account, user=request.user)
    used = sent_today(account)

    if request.method == "POST":

        # Validate one-time submission token
        if request.POST.get("token") != request.session.get("send_token"):
            messages.error(
                request,
                "That transfer was already submitted or the page expired."
            )
            request.session["send_token"] = uuid.uuid4().hex
            return redirect("transfers:send")

        form = TransferForm(
            request.POST,
            account=account,
            used_today=used
        )

        if form.is_valid():
            beneficiary = form.cleaned_data.get("beneficiary")
            account_number = form.cleaned_data.get("account_number")
            amount = form.cleaned_data["amount"]
            note = form.cleaned_data.get("description", "")
            pin = form.cleaned_data["pin"]

            # Resolve recipient number from either input method
            recipient_number = (
                beneficiary.account_number
                if beneficiary
                else account_number
            )

            reference = "KL" + uuid.uuid4().hex[:10].upper()

            description = (
                f"Transfer to {beneficiary.name}"
                if beneficiary
                else f"Transfer to {recipient_number}"
            )

            if note:
                description += f": {note}"

            description = description[:255]

            pin_error = None

            try:
                with db.atomic():

                    # Find recipient before changing any balance
                    recipient_id = (
                        Account.objects
                        .filter(account_number=recipient_number)
                        .values_list("pk", flat=True)
                        .first()
                    )

                    if not recipient_id:
                        raise SendFailed(
                            "Recipient account was not found."
                        )

                    if recipient_id == account.pk:
                        raise SendFailed(
                            "You cannot transfer to your own account."
                        )

                    # Lock both accounts in consistent PK order
                    account_ids = sorted([account.pk, recipient_id])

                    locked_accounts = list(
                        Account.objects
                        .select_for_update()
                        .filter(pk__in=account_ids)
                        .order_by("pk")
                    )

                    accounts = {
                        item.pk: item
                        for item in locked_accounts
                    }

                    sender = accounts.get(account.pk)
                    recipient = accounts.get(recipient_id)

                    if not sender or not recipient:
                        raise SendFailed(
                            "Unable to verify both accounts."
                        )

                    # Check account lock and PIN
                    if sender.is_locked():
                        pin_error = (
                            "Your transaction PIN is locked. "
                            "Try again after 15 minutes."
                        )

                    elif not sender.pin_hash:
                        pin_error = (
                            "Set your transaction PIN before transferring."
                        )

                    elif not sender.check_pin(pin):
                        pin_error = (
                            "Incorrect transaction PIN. "
                            "Check your PIN and try again."
                        )

                    # Do not raise here: failed PIN attempts must be saved.
                    if not pin_error:

                        # Recheck balance and daily limit under the lock
                        if amount <= 0:
                            raise SendFailed(
                                "Amount must be greater than zero."
                            )

                        if sender.currency != recipient.currency:
                            raise SendFailed(
                                "Recipient account currency does not match."
                            )

                        if sender.balance < amount:
                            raise SendFailed(
                                "Insufficient balance."
                            )

                        current_used = sent_today(sender)

                        if current_used + amount > DAILY_LIMIT:
                            raise SendFailed(
                                "Daily transfer limit reached."
                            )

                        # Update balances
                        sender.balance -= amount
                        recipient.balance += amount

                        sender.save(update_fields=["balance"])
                        recipient.save(update_fields=["balance"])

                        # Create transfer record
                        transfer = Transfer.objects.create(
                            sender=sender,
                            receiver=recipient,
                            amount=amount,
                            reference=reference,
                            status="completed"
                        )

                        # Sender transaction
                        Transaction.objects.create(
                            account=sender,
                            transaction_type="transfer",
                            amount=amount,
                            description=description
                        )

                        # Recipient transaction
                        Transaction.objects.create(
                            account=recipient,
                            transaction_type="deposit",
                            amount=amount,
                            description="Transfer received"
                        )

            except SendFailed as error:
                print(error)
                form.add_error("amount", str(error))

            else:
                if pin_error:
                    form.add_error("pin", pin_error)

                else:
                    request.session.pop("send_token", None)

                    messages.success(
                        request,
                        "Transfer successful."
                    )

                    return redirect(
                        "transaction:receipt",
                        reference=reference
                    )

    else:
        form = TransferForm(
            account=account,
            used_today=used
        )

    token = request.session.setdefault(
        "send_token",
        uuid.uuid4().hex
    )

    # Refresh daily usage after a failed PIN attempt or other validation
    used = sent_today(account)

    return render(
        request,
        "transfers/send_money.html",
        {
            "form": form,
            "account": account,
            "token": token,
            "daily_left": max(
                DAILY_LIMIT - used,
                Decimal("0.00")
            ),
        }
    )


    

@login_required
def request_money(request):
    receiver = request.user.account

    if request.method == "POST":
        form = RequestForm(request.POST)

        if form.is_valid():
            amount = form.cleaned_data["amount"]
            account_number = form.cleaned_data["account_number"]
            note = form.cleaned_data["note"]

            try:
                sender = Account.objects.get(
                    account_number=account_number
                )

                if sender.pk == receiver.pk:
                    messages.error(
                        request,
                        "You cannot request money from yourself."
                    )
                else:
                    MoneyRequest.objects.create(
                        requester=request.user,
                        recipient=sender.user,
                        amount=amount,
                        note=note,
                        status="pending"
                    )

                    messages.success(
                        request,
                        "Money request sent successfully."
                    )

                    return redirect("request_money")

            except Account.DoesNotExist:
                messages.error(
                    request,
                    "Recipient account was not found."
                )

    else:
        form = RequestForm()

    return render(
        request,
        "transfers/request.html",
        {"form": form}
    )