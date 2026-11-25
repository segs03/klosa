from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from .capital import capital_balance, retained_profit_balance
from .contributions import post_verified_investor_contribution
from .contributor import  create_intent, reject_intent
from .models import CapitalContribution, ContributionIntent, Investor, InvestorCapitalEntry
from .security import need


def _problem(e):
    if isinstance(e, ValidationError):
        return e.messages[0]
    return "Bad request." if isinstance(e, ValueError) else (str(e) or "Not allowed.")


class IntentForm(forms.Form):
    amount = forms.CharField(max_length=15)
    method = forms.ChoiceField(choices=ContributionIntent.METHODS)


@login_required
def my_deposits(request):
    investor = get_object_or_404(Investor, user=request.user, is_active=True)
    form = IntentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            i = create_intent(request.user, form.cleaned_data["amount"], form.cleaned_data["method"])
        except ValidationError as e:
            form.add_error("amount", e.messages[0])
        else:
            messages.success(request, f"Deposit notice {i.reference} created. Use it as the payment reference.")
            return redirect("funding:my_deposits")
    return render(request, "funding/contributor.html", {
        "form": form, "investor": investor, "bank": getattr(settings, "COMPANY_BANK", {}),
        "intents": investor.intents.all()[:10], "capital": capital_balance(investor),
        "retained": retained_profit_balance(investor),
        "entries": InvestorCapitalEntry.objects.filter(investor=investor, posted_at__isnull=False)[:15]})


@login_required
def intents_queue(request):
    need(request.user, "funding.confirm_contributionintent")
    if request.method == "POST":
        try:
            pk = int(request.POST.get("pk", ""))
            if request.POST.get("action") == "reject":
                reject_intent(pk, request.user, request.POST.get("reason", ""))
            else:
                confirm_received(pk, request.user)
        except (ValidationError, PermissionDenied, ValueError) as e:
            messages.error(request, _problem(e))
        else:
            messages.success(request, "Saved. The investor has been told.")
        return redirect("funding:intents")
    return render(request, "funding/intents.html", {
        "items": ContributionIntent.objects.filter(status__in=("pending", "expired")).select_related("investor")[:100]})


@login_required
def contributions_queue(request):
    need(request.user, "funding.verify_capitalcontribution")
    if request.method == "POST":
        try:
            post_verified_investor_contribution(int(request.POST.get("pk", "")), request.user)
        except (ValidationError, PermissionDenied, ValueError) as e:
            messages.error(request, _problem(e))
        else:
            messages.success(request, "Verified and posted to the ledger.")
        return redirect("funding:contributions")
    return render(request, "funding/contributions.html", {
        "items": CapitalContribution.objects.filter(source_type="investor", verified=False)
                 .select_related("investor", "recorded_by")[:100]})