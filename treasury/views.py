import uuid

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import Account
from accounts.utils import hit
from . import rules
from .services import TreasuryError, create_cash_in, pay_cash_out, request_cash_out

from django.conf import settings




def _need(user, perm):
    if not (user.is_active and user.is_staff and user.has_perm(perm)):
        raise PermissionDenied


def _page(request, title, form, button):
    return render(request, "accounts/form_page.html", {"title": title, "form": form, "button": button})


class CashOutForm(forms.Form):
    amount = forms.CharField(label="Amount (₦)", max_length=15)
    pin = forms.CharField(label="Transaction PIN", min_length=4, max_length=4, strip=False,
                          widget=forms.PasswordInput(attrs={"inputmode": "numeric", "autocomplete": "off"}))

    def clean_pin(self):
        pin = self.cleaned_data["pin"]
        if not (pin.isascii() and pin.isdigit()):
            raise forms.ValidationError("PIN must contain exactly 4 digits.")
        return pin


class PayForm(forms.Form):
    account_number = forms.CharField(label="Customer's account number", max_length=10)
    code = forms.CharField(label="Withdrawal code", min_length=6, max_length=6,
                           widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "off"}))


class CashInForm(forms.Form):
    account_number = forms.CharField(label="Account number", max_length=10)
    amount = forms.CharField(label="Cash received (₦)", max_length=15)
    branch = forms.CharField(label="Branch or agent", max_length=60)
    note = forms.CharField(label="Note (optional)", max_length=200, required=False)


#@login_required
#def deposit_info(request):
#    account = get_object_or_404(Account, user=request.user)
#    return render(request, "treasury/deposit_info.html", {"account": account, "min": rules.CASHIN_MIN})
#    
 
@login_required
def deposit_info(request):
    account = get_object_or_404(Account, user=request.user)
    provider = str(getattr(settings, "CASHIER_PROVIDER", ""))
    return render(request, "treasury/deposit_info.html", {
        "account": account, "min": rules.CASHIN_MIN,
        "card_on": "mock" not in provider.lower() or settings.DEBUG,
        "transfer_on": getattr(settings, "CASHIER_INBOUND_TRANSFERS", False)})


@login_required
def cash_out(request):
    account = get_object_or_404(Account, user=request.user)
    ref = request.session.setdefault("co_ref", "CO" + uuid.uuid4().hex[:10].upper())
    form = CashOutForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if hit(f"cashout:{request.user.pk}", 10, 3600):
            form.add_error("amount", "Too many attempts. Try again later.")
        else:
            try:
                co, code = request_cash_out(account, amount=form.cleaned_data["amount"],
                                           reference=ref, pin=form.cleaned_data["pin"])
            except TreasuryError as e:
                msg = str(e)
                form.add_error("pin" if ("PIN" in msg or "locked" in msg) else "amount", msg)
            else:
                request.session.pop("co_ref", None)
                return render(request, "treasury/cashout_done.html", {"co": co, "code": code, "account": account})
    return _page(request, "Withdraw cash", form, "Get my code")


@login_required
def teller_pay(request):
    _need(request.user, "treasury.pay_cashout")
    form = PayForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            co = pay_cash_out(request.user, form.cleaned_data["account_number"], form.cleaned_data["code"])
        except TreasuryError as e:
            form.add_error(None, str(e))
        else:
            name = co.account.user.get_full_name().strip() or "the customer"
            messages.success(request, f"Pay ₦{co.amount:,.2f} in cash to {name}. Check their ID first.")
            return redirect("treasury:pay")
    return _page(request, "Pay out cash", form, "Pay out")


@login_required
def teller_cash_in(request):
    _need(request.user, "treasury.record_cash")
    form = CashInForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            req = create_cash_in(request.user, d["account_number"], d["amount"], d["branch"], d["note"])
        except TreasuryError as e:
            form.add_error(None, str(e))
        else:
            messages.success(request, f"Recorded {req.reference}. A second staff member must approve it in the admin.")
            return redirect("treasury:cash_in")
    return _page(request, "Record cash received", form, "Record")