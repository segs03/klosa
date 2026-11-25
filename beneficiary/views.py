import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from accounts.models import Account
from accounts.utils import hit
from .banks import KLOSA
from .forms import MAX_BENEFICIARIES, BeneficiaryForm
from .models import Beneficiary
from django.conf import settings


@login_required
def beneficiary_list(request):
    q = (request.GET.get("q") or "").strip()[:60]
    items = Beneficiary.objects.filter(owner=request.user, is_active=True)
    total = items.count()
    if q:
        items = items.filter(Q(name__icontains=q) | Q(bank_name__icontains=q) | Q(account_number__endswith=q))
    return render(request, "beneficiary/list.html",
                  {"beneficiaries": items, "q": q, "total": total, "limit": MAX_BENEFICIARIES, "klosa_code": settings.KLOSA_BANK_CODE})


@login_required
def add_beneficiary(request):
    form = BeneficiaryForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        name = form.holder or d["name"]
        b, created = Beneficiary.objects.get_or_create(
            owner=request.user, bank_code=d["bank_code"], account_number=d["account_number"],
            defaults={"name": name, "bank_name": d["bank_name"]})
        if not created:                          # removed earlier: bring it back
            b.name, b.bank_name, b.is_active = name, d["bank_name"], True
            b.save()
        messages.success(request, "Recipient added.")
        return redirect("beneficiary:list")
    return render(request, "beneficiary/add.html", {"form": form})


@login_required
@require_GET
def lookup(request):
    """Name check for Klosa accounts, so the person can confirm who they are saving.
    Limited per user, and a closed account looks the same as one that does not exist."""
    if hit(f"benlookup:{request.user.pk}", 20, 3600):
        return JsonResponse({"error": "Too many lookups. Try again later."}, status=429)
    number = (request.GET.get("number") or "").strip()
    if (request.GET.get("bank") or "") != KLOSA or not re.fullmatch(r"\d{10}", number):
        return JsonResponse({"found": False})
    target = (Account.objects.select_related("user")
              .filter(account_number=number, closed_at__isnull=True, user__is_active=True).first())
    if target is None:
        return JsonResponse({"found": False})
    if target.user_id == request.user.pk:
        return JsonResponse({"found": False, "own": True})
    return JsonResponse({"found": True, "name": target.user.get_full_name().strip() or "Klosa customer"})


@login_required
@require_POST
def delete_beneficiary(request, pk):
    b = get_object_or_404(Beneficiary, pk=pk, owner=request.user, is_active=True)
    b.is_active = False                          # soft delete keeps transaction history
    b.save(update_fields=["is_active"])
    messages.success(request, "Recipient removed.")
    return redirect("beneficiary:list")