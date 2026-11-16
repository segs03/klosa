import json
import logging
import uuid
import hashlib
import hmac
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.models import Account
from accounts.services import limits_for
from accounts.utils import hit
from beneficiary.banks import bank_code_for
from . import rules
from .forms import DepositForm, WithdrawForm
from .models import Deposit, Withdrawal
from .providers.base import Result
from .providers.mock import MockCashProvider
from .services import (CashierError, apply_payout, credit_deposit, credit_inbound_transfer, get_provider,
                       new_ref, signature_ok, start_deposit, start_withdrawal)



log = logging.getLogger(__name__)
QUICK = [1000, 2000, 5000, 10000, 20000]


def _account(request):
    return get_object_or_404(Account, user=request.user)


def _when(dt):
    return timezone.localtime(dt).strftime("%d %b %Y, %H:%M")


# ------------------------------------------------------------------ deposits
@login_required
def deposit(request):
    acc = _account(request)
    if acc.closed_at:
        messages.error(request, "Your account is closed.")
        return redirect("accounts:home")
    form = DepositForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if hit(f"dep:{request.user.pk}", 10, 3600):
            form.add_error("amount", "Too many attempts. Try again later.")
        else:
            try:
                dep = start_deposit(acc, form.cleaned_data["amount"])
            except CashierError as e:
                form.add_error("amount", str(e))
            else:
                return redirect(dep.checkout_url)
    return render(request, "cashier/deposit.html", {
        "form": form, "account": acc, "quick": QUICK, "min": rules.DEPOSIT_MIN, "max": rules.DEPOSIT_MAX})


DEP_TEXT = {
    "pending": ("state_processing", "Waiting for payment", "Finish paying on the payment page. This page updates by itself."),
    "completed": ("completed", "Money added", "Your balance has been updated."),
    "failed": ("reversed", "Payment failed", "No money was taken. You can try again."),
    "expired": ("reversed", "Payment expired", "It was not completed in time. If you were charged, contact support."),
    "review": ("state_processing", "Under review", "We received a payment that needs a quick check. Our team will sort it out."),
}


@login_required
def deposit_status(request, reference):
    dep = get_object_or_404(Deposit, reference=reference, account__user=request.user)
    state, title, text = DEP_TEXT[dep.status]
    rows = [("Method", dep.get_method_display()), ("Reference", dep.reference), ("Date", _when(dep.created_at))]
    return render(request, "cashier/status.html", {
        "state": "processing" if state == "state_processing" else state, "title": title, "text": text,
        "amount": dep.paid_amount or dep.amount, "rows": rows, "raw_status": dep.status,
        "poll_url": request.path + "poll/" if dep.status == "pending" else "",
        "resume_url": dep.checkout_url if dep.status == "pending" else "",
        "again_url": "/cash/deposit/", "again_label": "Add more money"})


@login_required
@require_POST
def deposit_poll(request, reference):
    dep = get_object_or_404(Deposit, reference=reference, account__user=request.user)
    if dep.status == "pending" and not hit(f"dep_check:{dep.pk}", 1, 15):       # one real check every 15 seconds
        try:
            r = get_provider().verify_deposit(dep)
            if r.status == "success":
                credit_deposit(dep.reference, r.amount, r.provider_reference)
        except Exception:
            log.exception("Deposit check failed for %s", dep.reference)
    dep.refresh_from_db(fields=["status"])
    return JsonResponse({"status": dep.status})


@login_required
def mock_pay(request, reference):
    """A fake payment page for testing. It only exists while the mock provider and the flag are on."""
    if not (getattr(settings, "CASHIER_ALLOW_MOCK_PAYPAGE", False) and isinstance(get_provider(), MockCashProvider)):
        raise Http404
    dep = get_object_or_404(Deposit, reference=reference, account__user=request.user)
    if dep.status != "pending":
        return redirect("cashier:deposit_status", reference=dep.reference)
    if request.method == "POST":
        if request.POST.get("action") == "pay":
            MockCashProvider.paid[dep.reference] = dep.amount
            credit_deposit(dep.reference, dep.amount, "MOCK-" + dep.reference)
        return redirect("cashier:deposit_status", reference=dep.reference)
    return render(request, "cashier/mockpay.html", {"dep": dep})


@csrf_exempt
@require_POST
def webhook(request):
    body = request.body
    if len(body) > 20000:
        return HttpResponse(status=413)
    if not signature_ok(body, request.headers.get("X-Signature", "")):
        return HttpResponse(status=401)
    try:
        data = json.loads(body)
        event = data["event"]
        if event == "deposit.success":
            credit_deposit(data["reference"], data["amount"], data.get("provider_reference", ""))
        elif event == "transfer.received":
            credit_inbound_transfer(data["account_number"], data["amount"], data["provider_reference"],
                                    data.get("sender", ""))
        elif event in ("payout.success", "payout.failed"):
            w = Withdrawal.objects.filter(reference=data["reference"]).first()
            if w:
                apply_payout(w.pk, Result("success" if event == "payout.success" else "failed",
                                          data.get("provider_reference", ""), message=data.get("message", "")))
    except (ValueError, KeyError, TypeError, CashierError):
        return HttpResponse(status=400)
    return JsonResponse({"ok": True})


# --------------------------------------------------------------- withdrawals
def _recent_payees(acc):
    seen, out = set(), []
    for w in Withdrawal.objects.filter(account=acc).order_by("-created_at")[:20]:
        if (w.bank_name, w.account_number) not in seen:
            seen.add((w.bank_name, w.account_number))
            out.append(w)
        if len(out) == 4:
            break
    return out


@login_required
def withdraw(request):
    acc = _account(request)
    if acc.closed_at:
        messages.error(request, "Your account is closed.")
        return redirect("accounts:home")
    if acc.kyc_tier < getattr(settings, "CASHIER_WITHDRAW_MIN_TIER", 1):
        messages.error(request, "Verify your ID to withdraw money.")
        return redirect("accounts:home")

    provider = get_provider()
    token = request.session.setdefault("wd_token", uuid.uuid4().hex)
    ref = request.session.setdefault("wd_ref", new_ref("WD"))
    form = WithdrawForm(request.POST or None)
    base = {"form": form, "account": acc, "recent": _recent_payees(acc),
            "single": limits_for(acc)["single"], "fee": rules.WITHDRAW_FEE}

    if request.method != "POST" or not form.is_valid():
        return render(request, "cashier/withdraw.html", base)

    d = form.cleaned_data
    bank, number, amount = d["bank_name"], d["account_number"], d["amount"]
    step = request.POST.get("step") or ("pay" if request.POST.get("pin") else "review")
    if step == "edit":
        return render(request, "cashier/withdraw.html", base)

    def review_page(name, error=""):
        return render(request, "cashier/withdraw_review.html", {
            "bank": bank, "number": number, "name": name, "amount": amount, "fee": rules.WITHDRAW_FEE,
            "total": amount + rules.WITHDRAW_FEE, "token": token, "error": error})

    if step != "pay":
        if hit(f"wd_name:{request.user.pk}", 30, 3600):
            form.add_error("account_number", "Too many lookups. Try again in an hour.")
            return render(request, "cashier/withdraw.html", base)
        try:
            name = provider.name_enquiry(bank_code_for(bank), number)
        except Exception:
            log.exception("Name lookup failed")
            name = None
        if not name:
            form.add_error("account_number", "We couldn't confirm that account. Check the number and bank.")
            return render(request, "cashier/withdraw.html", base)
        request.session["wd_verified"] = {"bank": bank, "number": number, "name": name}
        return review_page(name)

    if request.POST.get("token") != token:
        messages.error(request, "That withdrawal was already submitted or the page expired.")
        return redirect("cashier:withdraw")
    verified = request.session.get("wd_verified")
    if not verified or verified["bank"] != bank or verified["number"] != number:
        form.add_error("account_number", "Please check the account again before paying.")
        return render(request, "cashier/withdraw.html", base)
    try:
        w = start_withdrawal(acc, bank_name=bank, account_number=number, account_name=verified["name"],
                             amount=amount, reference=ref, pin=request.POST.get("pin", ""))
    except CashierError as e:
        if str(e) == "Duplicate reference.":
            for k in ("wd_token", "wd_ref", "wd_verified"):
                request.session.pop(k, None)
            messages.error(request, "That withdrawal was already submitted. Check your history.")
            return redirect("accounts:home")
        return review_page(verified["name"], str(e))

    for k in ("wd_token", "wd_ref", "wd_verified"):
        request.session.pop(k, None)
    if w.status == "completed":
        messages.success(request, "Withdrawal sent.")
    elif w.status == "reversed":
        messages.error(request, "The bank could not complete this. You were refunded.")
    else:
        messages.info(request, "Your withdrawal is being processed.")
    return redirect("cashier:withdraw_order", reference=w.reference)


WD_TEXT = {
    "processing": ("processing", "Processing", "Your money is held while we confirm with the bank. This usually takes a few minutes."),
    "completed": ("completed", "Withdrawal sent", "The money is on its way to the account below."),
    "reversed": ("reversed", "Withdrawal refunded", "The bank could not complete this, so your money is back in your account."),
}


@login_required
def withdraw_order(request, reference):
    w = get_object_or_404(Withdrawal, reference=reference, account__user=request.user)
    state, title, text = WD_TEXT[w.status]
    rows = [("Bank", w.bank_name), ("Account", f"{w.account_name} ••••{w.account_number[-4:]}"),
            ("Amount", f"₦{w.amount:,.2f}")]
    if w.fee:
        rows.append(("Fee", f"₦{w.fee:,.2f}"))
    rows += [("Reference", w.reference), ("Date", _when(w.created_at))]
    return render(request, "cashier/status.html", {
        "state": state, "title": title, "text": text, "amount": w.amount + w.fee, "rows": rows,
        "raw_status": w.status, "poll_url": request.path + "poll/" if w.status == "processing" else "",
        "again_url": "/cash/withdraw/", "again_label": "Withdraw again"})


@login_required
@require_POST
def withdraw_poll(request, reference):
    w = get_object_or_404(Withdrawal, reference=reference, account__user=request.user)
    if w.status == "processing" and not hit(f"wd_check:{w.pk}", 1, 15):
        try:
            from .services import settle_withdrawal
            if w.submitted_at is None:
                settle_withdrawal(w.pk)
            else:
                apply_payout(w.pk, get_provider().requery_payout(w))
        except Exception:
            log.exception("Withdrawal check failed for %s", w.reference)
    w.refresh_from_db(fields=["status"])
    return JsonResponse({"status": w.status})
    
    
   
# PAYSTACK 
@csrf_exempt
@require_POST
def paystack_webhook(request):
    body = request.body
    secret = getattr(settings, "PAYSTACK_SECRET_KEY", "")
    sig = request.headers.get("X-Paystack-Signature", "")
    if len(body) > 50000:
        return HttpResponse(status=413)
    if not secret or not sig or not hmac.compare_digest(hmac.new(secret.encode(), body, hashlib.sha512).hexdigest(), sig):
        return HttpResponse(status=401)
    try:
        data = json.loads(body)
        if data.get("event") == "charge.success":
            d = data["data"]
            credit_deposit(d["reference"], Decimal(str(d["amount"])) / 100, str(d.get("id", "")))
    except (ValueError, KeyError, TypeError, ArithmeticError):
        return HttpResponse(status=400)
    return JsonResponse({"ok": True})