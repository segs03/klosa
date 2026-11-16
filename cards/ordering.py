import uuid

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.credit_services import credit_summary
from accounts.models import Account, VerifiedIdentity
from accounts.utils import hit
from .models import BankCard, CardOrder
from .services import authorize_purchase, card_name, place_order


class OrderForm(forms.Form):
    card_type = forms.ChoiceField(choices=CardOrder.CARD_TYPES)
    card_format = forms.ChoiceField(choices=CardOrder.CARD_FORMATS)
    cardholder_name = forms.CharField(required=False, max_length=60)
    address = forms.CharField(required=False, max_length=250)
    city = forms.CharField(required=False, max_length=100)
    state = forms.CharField(required=False, max_length=100)
    phone = forms.CharField(required=False, max_length=20)


@login_required
def order_card(request):
    account = get_object_or_404(Account, user=request.user)
    ident = VerifiedIdentity.objects.select_related("source_application").filter(user=request.user).first()
    form = OrderForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if hit(f"cardorder:{request.user.pk}", 10, 3600):
            form.add_error(None, "Too many attempts. Try again later.")
        else:
            try:
                place_order(account, **form.cleaned_data)
            except ValidationError as e:
                form.add_error(None, e.messages[0])
            else:
                messages.success(request, "Your card request was received. We will review it shortly.")
                return redirect(reverse("accounts:home") + "#card")
    return render(request, "cards/order.html", {
        "form": form, "account": account, "credit": credit_summary(account), "verified": bool(ident),
        "verified_name": card_name(ident.source_application.legal_name) if ident else ""})


class SimForm(forms.Form):
    amount = forms.CharField(max_length=15)
    merchant = forms.CharField(required=False, max_length=60, label="Merchant (optional)")


@login_required
def simulate_purchase(request):
    if not getattr(settings, "CARDS_ALLOW_SIMULATOR", False):
        raise Http404
    account = get_object_or_404(Account, user=request.user)
    card = BankCard.objects.filter(account=account, card_type="credit", status="active").first()
    if card is None:
        messages.error(request, "You have no active credit card.")
        return redirect("accounts:home")
    form = SimForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        ok, why = authorize_purchase(card, form.cleaned_data["amount"], "SIM" + uuid.uuid4().hex[:20],
                                     form.cleaned_data["merchant"] or "Test merchant", "pos")
        (messages.success if ok else messages.error)(
            request, "Approved. Your available credit went down." if ok else f"Declined: {why}")
        return redirect(reverse("accounts:home") + "#card")
    return render(request, "accounts/form_page.html", {
        "title": "Test a purchase", "form": form, "button": "Pay with credit card"})