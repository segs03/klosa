import logging
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Account
from accounts.utils import hit
from .forms import DetailsForm
from .models import BillPayment
from .rules import FEES, RULES
from .services import LABELS, PaymentError, apply_result, get_provider, new_reference, settle, start_payment

log = logging.getLogger(__name__)

SERVICES = [
    {"key": "airtime", "title": "Airtime", "blurb": "Top up any network", "ref_label": "Phone number",
     "ref_hint": "080 1234 5678", "ref_mode": "tel", "quick": [100, 200, 500, 1000, 2000],
     "path": "M8 2.5h8a2 2 0 0 1 2 2v15a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2v-15a2 2 0 0 1 2-2zM11 18h2"},
    {"key": "data", "title": "Data", "blurb": "Bundles for any network", "ref_label": "Phone number",
     "ref_hint": "080 1234 5678", "ref_mode": "tel", "quick": [],
     "path": "M2 9a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 19.5h.01"},
    {"key": "education", "title": "Education", "blurb": "Exam pins and forms", "ref_label": "Phone number or ID",
     "ref_hint": "Candidate or phone number", "ref_mode": "text", "quick": [],
     "path": "M2 9l10-5 10 5-10 5-10-5zM6 11.5V16c0 1.5 3 3 6 3s6-1.5 6-3v-4.5"},
    {"key": "electricity", "title": "Electricity", "blurb": "Prepaid and postpaid", "ref_label": "Meter number",
     "ref_hint": "Meter number", "ref_mode": "text", "quick": [1000, 2000, 5000, 10000],
     "path": "M13 2L4 14h7l-1 8 9-12h-7l1-8z"},
    {"key": "tv", "title": "TV", "blurb": "Cable subscriptions", "ref_label": "Smartcard or IUC number",
     "ref_hint": "Smartcard number", "ref_mode": "text", "quick": [],
     "path": "M3 7h18v12H3zM8 3l4 4 4-4"},
    {"key": "internet", "title": "Internet", "blurb": "Home and office plans", "ref_label": "Account ID",
     "ref_hint": "Account ID", "ref_mode": "text", "quick": [],
     "path": "M4 14h16v6H4zM8 17h.01M12 17h.01M7 10a7 7 0 0 1 10 0M9.5 12.5a3.5 3.5 0 0 1 5 0"},
    {"key": "betting", "title": "Betting", "blurb": "Fund your wallet", "ref_label": "Betting account ID",
     "ref_hint": "Account ID", "ref_mode": "text", "quick": [500, 1000, 5000],
     "path": "M4 4h16v16H4zM8.5 8.5h.01M15.5 8.5h.01M12 12h.01M8.5 15.5h.01M15.5 15.5h.01"},
]
SERVICE_MAP = {s["key"]: s for s in SERVICES}


def _account(request):
    return get_object_or_404(Account, user=request.user)


@login_required
def hub(request):
    acc = _account(request)
    items = [dict(s, locked=acc.kyc_tier < RULES[s["key"]]["tier"]) for s in SERVICES]
    recent = BillPayment.objects.filter(account=acc)[:3]
    return render(request, "biller/hub.html", {"account": acc, "items": items, "recent": recent, "labels": LABELS})


def _lookup(request, provider, category, d):
    """(name, error). Phone-based services have no name lookup."""
    if RULES[category]["ref"] == "phone":
        return "", None
    if hit(f"bp_verify:{request.user.pk}", 30, 3600):
        return "", "Too many lookups. Try again in an hour."
    try:
        name = provider.verify_customer(category, d["provider"], d.get("product", ""), d["customer_ref"])
    except Exception:
        log.exception("Customer lookup failed")
        name = None
    if not name:
        return "", "We couldn't confirm that number. Check it and try again."
    return name, None


@login_required
def service(request, category):
    cfg = SERVICE_MAP.get(category)
    if not cfg:
        raise Http404
    acc = _account(request)
    if acc.closed_at:
        messages.error(request, "Your account is closed.")
        return redirect("accounts:home")
    rule = RULES[category]
    if acc.kyc_tier < rule["tier"]:
        messages.error(request, "Verify your ID to use this service.")
        return redirect("biller:hub")

    provider = get_provider()
    catalog = provider.catalog(category)
    if not catalog["providers"]:
        raise Http404

    token = request.session.setdefault("bp_token", uuid.uuid4().hex)
    ref = request.session.setdefault("bp_ref", new_reference())
    form = DetailsForm(request.POST or None, category=category, provider=provider, label=cfg["ref_label"])

    phone = request.user.phone_number or ""
    details = {
        "cfg": cfg, "rule": rule, "form": form, "providers": catalog["providers"],
        "product_list": [dict(p, provider=code) for code, items in catalog["products"].items() for p in items],
        "has_products": rule["product"], "my_number": ("0" + phone[4:]) if phone.startswith("+234") else "",
        "selected_provider": form["provider"].value() or (
            catalog["providers"][0]["code"] if len(catalog["providers"]) == 1 else ""),
        "selected_product": form["product"].value() or "",
    }

    if request.method != "POST":
        return render(request, "biller/service.html", details)
    if not form.is_valid():
        return render(request, "biller/service.html", details)

    d, step = form.cleaned_data, request.POST.get("step")
    if step == "edit":
        return render(request, "biller/service.html", details)

    name, error = _lookup(request, provider, category, d)
    if error:
        form.add_error("customer_ref", error)
        return render(request, "biller/service.html", details)

    fee = FEES.get(category, 0)
    review = {"cfg": cfg, "category": category, "d": d, "provider_name": form.provider_name,
              "product_name": form.product_name, "name": name, "amount": d["amount"], "fee": fee,
              "total": d["amount"] + fee, "token": token}

    if step != "pay":
        return render(request, "biller/review.html", review)

    if request.POST.get("token") != token:
        messages.error(request, "That payment was already submitted or the page expired.")
        return redirect("biller:history")
    try:
        order = start_payment(
            acc, category=category, provider_code=d["provider"], product_code=d.get("product", ""),
            customer_ref=d["customer_ref"], amount=d["amount"], reference=ref,
            pin=request.POST.get("pin", ""))
    except PaymentError as e:
        if str(e) == "Duplicate reference.":
            request.session.pop("bp_token", None)
            request.session.pop("bp_ref", None)
            messages.error(request, "That payment was already submitted. Check your history.")
            return redirect("biller:history")
        review["error"] = str(e)
        return render(request, "biller/review.html", review)

    request.session.pop("bp_token", None)
    request.session.pop("bp_ref", None)
    return redirect("biller:order", reference=order.reference)


@login_required
def order_detail(request, reference):
    order = get_object_or_404(BillPayment, reference=reference, account__user=request.user)
    return render(request, "biller/order.html", {
        "order": order, "label": LABELS.get(order.category, "Bill"), "total": order.amount + order.fee,
        "cfg": SERVICE_MAP.get(order.category)})


@login_required
@require_POST
def order_check(request, reference):
    order = get_object_or_404(BillPayment, reference=reference, account__user=request.user)
    if order.status == "processing":
        if hit(f"bp_check:{order.pk}", 1, 15):                      # one check every 15 seconds
            messages.info(request, "Please wait a few seconds before checking again.")
        else:
            try:
                if order.submitted_at is None:
                    settle(order.pk)
                else:
                    apply_result(order.pk, get_provider().requery(order))
            except Exception:
                log.exception("Status check failed for %s", order.reference)
                messages.error(request, "We couldn't check right now. Try again shortly.")
    return redirect("biller:order", reference=order.reference)


@login_required
def history(request):
    orders = BillPayment.objects.filter(account__user=request.user)[:50]
    return render(request, "biller/history.html", {"orders": orders, "labels": LABELS})