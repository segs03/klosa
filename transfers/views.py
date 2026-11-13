import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Account
from accounts.services import limits_for
from .forms import RequestForm, TransferForm
from .models import MoneyRequest
from .services import TransferError, pay_money_request, sent_today, send_money

MAX_PENDING_REQUESTS = 10
REQUEST_DAYS = 7


@login_required
def send_money_view(request):
    account = get_object_or_404(Account, user=request.user)
    if account.closed_at:
        messages.error(request, "Your account is closed.")
        return redirect("accounts:home")

    used = sent_today(account)
    lim = limits_for(account)

    if request.method == "POST":
        # one-time token: blocks double taps and refresh re-posts
        if request.POST.get("token") != request.session.get("send_token"):
            messages.error(request, "That transfer was already submitted or the page expired.")
            request.session["send_token"] = uuid.uuid4().hex
            return redirect("transfers:send")

        form = TransferForm(request.POST, account=account, used_today=used)

        if form.is_valid():
            beneficiary = form.cleaned_data.get("beneficiary")
            number = beneficiary.account_number if beneficiary else form.cleaned_data.get("account_number")
            amount = form.cleaned_data["amount"]
            note = form.cleaned_data.get("description", "")
            pin = form.cleaned_data["pin"]

            # the beneficiary's bank must be Klosa until you connect a payout provider
            if beneficiary and beneficiary.bank_code != settings.KLOSA_BANK_CODE:
                form.add_error("beneficiary", "Transfers to other banks are not available yet.")
            else:
                receiver = (Account.objects
                            .filter(account_number=number, closed_at__isnull=True)
                            .first())
                if not receiver:
                    form.add_error(None, "That account is not available.")
                else:
                    try:
                        # the reference is made once per page, so a retry of the same page can't pay twice
                        t = send_money(account, receiver, amount, request.session["send_ref"],
                                       pin, note=note, beneficiary=beneficiary)
                    except TransferError as e:
                        msg = str(e)
                        field = "pin" if ("PIN" in msg or "locked" in msg) else "amount"
                        form.add_error(field, msg)
                    else:
                        request.session.pop("send_token", None)
                        request.session.pop("send_ref", None)
                        messages.success(request, "Transfer successful.")
                        return redirect("transactions:receipt", reference=t.reference)

            used = sent_today(account)                      # refresh after a failed try
    else:
        form = TransferForm(account=account, used_today=used)

    token = request.session.setdefault("send_token", uuid.uuid4().hex)
    request.session.setdefault("send_ref", "KL" + uuid.uuid4().hex[:10].upper())

    return render(request, "transfers/send_money.html", {
        "form": form, "account": account, "token": token,
        "daily_left": max(lim["daily"] - used, Decimal("0.00")),
    })


@login_required
def request_money(request):
    account = get_object_or_404(Account, user=request.user)
    form = RequestForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        amount = form.cleaned_data["amount"]
        number = form.cleaned_data["account_number"]
        note = form.cleaned_data.get("note", "")

        pending = MoneyRequest.objects.filter(requester=request.user, status="pending").count()
        target = (Account.objects.select_related("user")
                  .filter(account_number=number, closed_at__isnull=True, user__is_active=True)
                  .exclude(pk=account.pk).first())

        if pending >= MAX_PENDING_REQUESTS:
            messages.error(request, "You have too many open requests. Wait for some to be paid or cancel them.")
        elif target:
            MoneyRequest.objects.create(
                requester=request.user, recipient=target.user, amount=amount, note=note,
                status="pending", expires_at=timezone.now() + timedelta(days=REQUEST_DAYS))
            messages.success(request, "Money request sent.")
            return redirect("transfers:request_money")
        else:
            # same message for a wrong number and a closed account, so numbers can't be scanned
            messages.success(request, "Money request sent.")
            return redirect("transfers:request_money")

    return render(request, "transfers/request.html", {"form": form})


@login_required
def requests_inbox(request):
    mine = (MoneyRequest.objects
            .filter(recipient=request.user, status="pending")
            .select_related("requester").order_by("-created_at"))
    return render(request, "transfers/requests.html", {"requests": mine})


@login_required
@require_POST
def pay_request(request, pk):
    account = get_object_or_404(Account, user=request.user)
    pin = request.POST.get("pin", "")
    req = get_object_or_404(MoneyRequest, pk=pk, recipient=request.user)

    if account.is_locked():
        messages.error(request, "Your transaction PIN is locked. Try again later.")
    elif not account.pin_hash:
        messages.error(request, "Set your transaction PIN first.")
    elif not account.check_pin(pin):
        messages.error(request, "Incorrect transaction PIN.")
    else:
        try:
            pay_money_request(req.pk, request.user)
            messages.success(request, "Request paid.")
        except TransferError as e:
            messages.error(request, str(e))
    return redirect("transfers:requests")


@login_required
@require_POST
def decline_request(request, pk):
    req = get_object_or_404(MoneyRequest, pk=pk, recipient=request.user, status="pending")
    req.status = "declined"
    req.save(update_fields=["status"])
    messages.info(request, "Request declined.")
    return redirect("transfers:requests")


@login_required
@require_POST
def cancel_request(request, pk):
    req = get_object_or_404(MoneyRequest, pk=pk, requester=request.user, status="pending")
    req.status = "cancelled"
    req.save(update_fields=["status"])
    messages.info(request, "Request cancelled.")
    return redirect("transfers:request_money")