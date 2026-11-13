
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.shortcuts import render, redirect

from .forms import BeneficiaryForm
from .models import Beneficiary
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST


from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import BeneficiaryForm
from .models import Beneficiary


@login_required
def beneficiary_list(request):
    items = Beneficiary.objects.filter(owner=request.user, is_active=True)
    return render(request, "beneficiary/list.html", {"beneficiaries": items})


@login_required
def add_beneficiary(request):
    form = BeneficiaryForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        name = form.holder or d["name"]
        b, created = Beneficiary.objects.get_or_create(
            owner=request.user, bank_code=d["bank_code"], account_number=d["account_number"],
            defaults={"name": name, "bank_name": d["bank_name"]})
        if not created:                          # was removed earlier: bring it back
            b.name, b.bank_name, b.is_active = name, d["bank_name"], True
            b.save()
        messages.success(request, "Beneficiary added.")
        return redirect("beneficiary:list")
    return render(request, "beneficiary/add.html", {"form": form})


@login_required
@require_POST
def delete_beneficiary(request, pk):
    b = get_object_or_404(Beneficiary, pk=pk, owner=request.user, is_active=True)
    b.is_active = False                          # soft delete keeps transaction history
    b.save(update_fields=["is_active"])
    messages.success(request, "Beneficiary removed.")
    return redirect("beneficiary:list")