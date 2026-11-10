from django.contrib import messages
from django.contrib.auth.decorators import (
    login_required,
    permission_required,
)
from django.core.exceptions import ValidationError, PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from funding.forms import PeriodReopeningForm
from funding.models import FundingPeriod
from funding.period_reopening import reopen_funding_period

from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render


@login_required
@permission_required(
    "funding.view_fundingperiod",
    raise_exception=True,
)
def period_list_view(request):
    periods = FundingPeriod.objects.order_by("-start_date")

    return render(
        request,
        "funding/period_list.html",
        {"periods": periods},
    )




@login_required
@permission_required(
    "funding.reopen_fundingperiod",
    raise_exception=True,
)
@require_http_methods(["GET", "POST"])
def reopen_period_view(request, period_id):
    period = get_object_or_404(FundingPeriod, pk=period_id)

    if request.method == "POST":
        form = PeriodReopeningForm(request.POST)

        if form.is_valid():
            try:
                reopen_funding_period(
                    period_id=period.pk,
                    reopened_by=request.user,
                    reason=form.cleaned_data["reason"],
                )

                messages.success(
                    request,
                    "Funding period reopened successfully.",
                )
                return redirect("funding:periods")

            except (ValidationError, PermissionDenied) as error:
                messages.error(request, str(error))

    else:
        form = PeriodReopeningForm()

    return render(
        request,
        "funding/reopen_period.html",
        {
            "period": period,
            "form": form,
        },
    )