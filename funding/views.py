from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST
from django import forms

from funding.allocation import (
    approve_investor_allocation,
    post_allocation_to_capital,
    reject_investor_allocation,
)
from funding.forms import AllocationProposalForm, PeriodReopeningForm
from funding.models import FundingPeriod, InvestorAllocation
from funding.period import close_funding_period, start_period_review,open_funding_period
from funding.period_reopening import reopen_funding_period
from funding.services import allocate_monthly_result
from funding.allocation import post_approved_allocation
from backoffice.audit import log as audit_log




def _problem(error):
    if isinstance(error, ValidationError):
        return error.messages[0]
    return str(error) or "Not allowed."
    


class DatesForm(forms.Form):
    start_date = forms.DateField()
    end_date = forms.DateField()



@login_required
@require_POST
def period_open_view(request):
    form = DatesForm(request.POST)
    try:
        if not form.is_valid():
            raise ValidationError("Enter a start and an end date.")
        open_funding_period(start_date=form.cleaned_data["start_date"], end_date=form.cleaned_data["end_date"],
                            performed_by=request.user)
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, _problem(e))
    else:
        messages.success(request, "Period opened.")
    return redirect("funding:periods")


@login_required
@permission_required("funding.view_fundingperiod", raise_exception=True)
def period_detail_view(request, period_id):
    period = get_object_or_404(FundingPeriod, pk=period_id)
    if request.method == "POST":
        action, user = request.POST.get("action", ""), request.user
        try:
            if action == "review":
                start_period_review(period_id=period.pk, performed_by=user)
                audit_log(user, "period_review", f"period:{period.pk}")
            elif action == "close":
                close_funding_period(period_id=period.pk, performed_by=user)
                audit_log(user, "period_closed", f"period:{period.pk}")
            elif action == "propose":
                allocate_monthly_result(period.pk, request.POST.get("result", ""), proposed_by=user)
            elif action in ("approve", "post"):
                alloc = get_object_or_404(period.allocations, pk=int(request.POST.get("pk", "")))
                if action == "approve":
                    approve_investor_allocation(allocation_id=alloc.pk, approved_by=user)
                else:
                    post_approved_allocation(allocation_id=alloc.pk, posted_by=user)
            else:
                raise ValidationError("Choose an action.")
        except (ValidationError, PermissionDenied, ValueError) as e:
            messages.error(request, _problem(e))
        else:
            messages.success(request, "Done.")
        return redirect("funding:period", period_id=period.pk)
    rows = [{"a": a, "posted": a.capital_entries.exists()}
            for a in period.allocations.select_related("investor").order_by("investor__legal_name")]
    return render(request, "funding/period_detail.html", {"period": period, "rows": rows})


@login_required
@permission_required("funding.view_fundingperiod", raise_exception=True)
def period_list_view(request):
    periods = FundingPeriod.objects.order_by("-start_date")
    return render(request, "funding/period_list.html", {"periods": periods})


@login_required
@permission_required("funding.review_fundingperiod", raise_exception=True)
@require_POST
def period_review_view(request, period_id):
    period = get_object_or_404(FundingPeriod, pk=period_id)
    try:
        start_period_review(period_id=period.pk, performed_by=request.user)
    except (ValidationError, PermissionDenied) as error:
        messages.error(request, _message(error))
    else:
        messages.success(request, "The period is now under review.")
    return redirect("funding:periods")


@login_required
@permission_required("funding.close_fundingperiod", raise_exception=True)
@require_POST
def period_close_view(request, period_id):
    period = get_object_or_404(FundingPeriod, pk=period_id)
    try:
        close_funding_period(period_id=period.pk, performed_by=request.user)
    except (ValidationError, PermissionDenied) as error:
        messages.error(request, _message(error))
    else:
        messages.success(request, "The period is closed.")
    return redirect("funding:periods")


@login_required
@permission_required("funding.reopen_fundingperiod", raise_exception=True)
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
            except (ValidationError, PermissionDenied) as error:
                messages.error(request, _message(error))
            else:
                messages.success(request, "Funding period reopened successfully.")
                return redirect("funding:periods")
    else:
        form = PeriodReopeningForm()

    return render(request, "funding/reopen_period.html", {"period": period, "form": form})


@login_required
@permission_required("funding.view_fundingperiod", raise_exception=True)
@require_http_methods(["GET", "POST"])
def period_allocations_view(request, period_id):
    """Propose, approve, reject and post investor allocations for one period."""
    period = get_object_or_404(FundingPeriod, pk=period_id)
    form = AllocationProposalForm()

    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "propose":
                form = AllocationProposalForm(request.POST)
                if not form.is_valid():
                    raise ValidationError("Enter a valid amount.")
                rows = allocate_monthly_result(
                    period.pk, form.cleaned_data["result"], proposed_by=request.user
                )
                messages.success(request, f"{len(rows)} allocation(s) proposed. A different person must approve them.")
            elif action in {"approve", "reject", "post"}:
                allocation_id = int(request.POST.get("allocation", ""))
                # Only act on an allocation that belongs to this period.
                get_object_or_404(InvestorAllocation, pk=allocation_id, period=period)
                if action == "approve":
                    approve_investor_allocation(allocation_id=allocation_id, approved_by=request.user)
                    messages.success(request, "Allocation approved.")
                elif action == "reject":
                    reject_investor_allocation(allocation_id=allocation_id, rejected_by=request.user)
                    messages.success(request, "Allocation rejected.")
                else:
                    entries = post_allocation_to_capital(allocation_id=allocation_id, posted_by=request.user)
                    messages.success(request, f"Posted to the investor ledger ({len(entries)} entr{'y' if len(entries) == 1 else 'ies'}).")
            else:
                raise ValidationError("Unknown action.")
        except (ValidationError, PermissionDenied, ValueError) as error:
            messages.error(request, "Bad request." if isinstance(error, ValueError) else _message(error))
        else:
            return redirect("funding:allocations", period_id=period.pk)

    allocations = (InvestorAllocation.objects.filter(period=period)
                   .select_related("investor", "proposed_by", "approved_by")
                   .prefetch_related("capital_entries")
                   .order_by("investor__legal_name", "allocation_type"))
    rows = [{"a": a, "posted": a.capital_entries.all()} for a in allocations]
    return render(request, "funding/period_allocations.html",
                  {"period": period, "form": form, "rows": rows})
