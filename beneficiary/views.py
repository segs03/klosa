
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.shortcuts import render, redirect

from .forms import BeneficiaryForm
from .models import Beneficiary
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST


@login_required
def add_beneficiary(request):
    if request.method == "POST":
        form = BeneficiaryForm(request.POST)

        if form.is_valid():
            account_number = form.cleaned_data["account_number"]
            bank_name = form.cleaned_data["bank_name"]
            name = form.cleaned_data["name"]

            # Check for an existing beneficiary belonging
            # to this logged-in user.
            exists = Beneficiary.objects.filter(
                owner=request.user,
                account_number=account_number,
                bank_name=bank_name,
                is_active=True,
            ).exists()

            if exists:
                form.add_error(
                    "account_number",
                    "This beneficiary has already been saved.",
                )
            else:
                try:
                    Beneficiary.objects.create(
                        owner=request.user,
                        name=name,
                        account_number=account_number,
                        bank_name=bank_name,
                    )

                    messages.success(
                        request,
                        "Beneficiary saved successfully.",
                    )
                    return redirect("beneficiary:list")

                except IntegrityError:
                    messages.error(
                        request,
                        "This beneficiary could not be saved. "
                        "Please check the details and try again.",
                    )
    else:
        form = BeneficiaryForm()

    return render(
        request,
        "beneficiary/add.html",
        {"form": form},
    )


@login_required
def beneficiary_list(request):
    beneficiaries = Beneficiary.objects.filter(
        owner=request.user,
        is_active=True,
    )

    return render(
        request,
        "beneficiary/benefit.html",
        {"beneficiaries": beneficiaries},
    )




@login_required
@require_POST
def delete_beneficiary(request, beneficiary_id):
    beneficiary = get_object_or_404(
        Beneficiary,
        id=beneficiary_id,
        owner=request.user,
    )

    beneficiary.delete()

    messages.success(
        request,
        "Beneficiary deleted successfully.",
    )

    return redirect("beneficiary:list")
