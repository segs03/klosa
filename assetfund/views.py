from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from . import services as svc
from .forms import ApplyForm
from .models import AdvanceStage, AssetApplication, SaleRecord

STAFF_PERMS = ("assetfund.assess_assetapplication", "assetfund.approve_assetapplication",
               "assetfund.release_advancestage", "assetfund.confirm_assetsale")


def _problem(error):
    if isinstance(error, ValidationError):
        return error.messages[0]
    return str(error) or "Not allowed."


def _require_staff(user):
    if not (user.is_authenticated and user.is_active and user.is_staff and any(user.has_perm(p) for p in STAFF_PERMS)):
        raise PermissionDenied("You are not allowed to do this.")


def _run(request, work, done):
    """Run one service call; show its message either way."""
    try:
        work()
    except (ValidationError, PermissionDenied) as error:
        messages.error(request, _problem(error))
    except (ValueError, KeyError):
        messages.error(request, "That request was not valid.")
    except ObjectDoesNotExist:
        messages.error(request, "That item no longer exists.")
    else:
        messages.success(request, done)


# ---------------------------------------------------------------- customer

@login_required
def home(request):
    svc.expire_offers()
    return render(request, "assetfund/home.html", {
        "apps": request.user.asset_applications.all()[:20],
        "kinds": svc.RATE_CAPS, "customer_cap": svc.customer_cap(), "open": svc.total_cap() > 0})


@login_required
def apply(request):
    form = ApplyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            app = svc.submit_application(request.user, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error.messages[0])
        else:
            messages.success(request, "Application sent. We will inspect the asset and come back with an offer.")
            return redirect("assetfund:detail", pk=app.pk)
    return render(request, "assetfund/apply.html", {"form": form, "customer_cap": svc.customer_cap()})


@login_required
def detail(request, pk):
    app = get_object_or_404(AssetApplication, pk=pk, user=request.user)
    if request.method == "POST":
        post, action = request.POST, request.POST.get("action")
        if action == "accept":
            _run(request, lambda: svc.accept_offer(request.user, app.pk, post.get("pin", "")), "Offer accepted. Your first stage will be released after a final check.")
        elif action == "withdraw":
            _run(request, lambda: svc.withdraw_application(request.user, app.pk), "Application withdrawn.")
        elif action == "evidence":
            _run(request, lambda: svc.submit_stage_evidence(request.user, int(post.get("stage", "")), post.get("note", "")), "Progress sent. We will release the next stage after review.")
        elif action == "sale":
            _run(request, lambda: svc.report_sale(request.user, app.pk, post.get("proceeds", ""), post.get("note", ""),
                                                  is_final=post.get("is_final") == "on"), "Sale reported. We will confirm it shortly.")
        else:
            messages.error(request, "Unknown action.")
        return redirect("assetfund:detail", pk=app.pk)

    app = AssetApplication.objects.get(pk=app.pk)
    next_order = svc._next_order(app) if app.status == "active" else None
    evidence_stage = app.stages.filter(status="planned", order=next_order).exclude(order=1).first() if next_order else None
    return render(request, "assetfund/detail.html", {
        "app": app, "stages": app.stages.all(), "sales": app.sales.all(), "evidence_stage": evidence_stage,
        "summary": svc.offer_summary(app) if app.advance_amount else [],
        "can_report": app.status in ("active", "overdue")})


# ------------------------------------------------------------------- staff

@login_required
def staff_queue(request):
    _require_staff(request.user)
    svc.expire_offers()
    svc.mark_overdue()
    apps = AssetApplication.objects.select_related("user")
    stages = AdvanceStage.objects.filter(application__status="active").filter(
        status="requested").select_related("application", "application__user")
    first = AdvanceStage.objects.filter(application__status="active", order=1, status="planned").select_related(
        "application", "application__user")
    return render(request, "assetfund/staff_queue.html", {
        "to_assess": apps.filter(status="pending"), "to_decide": apps.filter(status="assessed"),
        "stages": list(first) + list(stages),
        "sales": SaleRecord.objects.filter(status="pending").select_related("application", "application__user"),
        "running": apps.filter(status__in=("active", "overdue")),
        "exposure": svc.exposure(), "cap": svc.total_cap()})


@login_required
def staff_detail(request, pk):
    _require_staff(request.user)
    app = get_object_or_404(AssetApplication.objects.select_related("user"), pk=pk)
    if request.method == "POST":
        post, action, me = request.POST, request.POST.get("action"), request.user
        if action == "assess":
            def work():
                plan = [(post.get(f"stage_title_{i}", "").strip(), post.get(f"stage_amount_{i}", "").strip())
                        for i in range(1, svc.MAX_STAGES + 1)
                        if post.get(f"stage_title_{i}", "").strip() or post.get(f"stage_amount_{i}", "").strip()]
                svc.assess_application(app.pk, me, verified_value=post.get("verified_value", ""),
                                       advance_rate=post.get("advance_rate", ""), fee_pct=post.get("fee_pct", ""),
                                       share_pct=post.get("share_pct", ""), term_days=int(post.get("term_days", "")),
                                       stages=plan, note=post.get("note", ""))
            _run(request, work, "Valuation saved. A second person must now approve it.")
        elif action in ("approved", "rejected"):
            _run(request, lambda: svc.decide_application(app.pk, me, action, post.get("reason", "")), "Decision saved.")
        elif action == "release":
            _run(request, lambda: svc.release_stage(int(post.get("stage", "")), me), "Stage released to the customer's account.")
        elif action == "confirm":
            _run(request, lambda: svc.confirm_sale(int(post.get("sale", "")), me, post.get("verified", "").strip() or None), "Sale confirmed.")
        elif action == "reject_sale":
            _run(request, lambda: svc.reject_sale(int(post.get("sale", "")), me, post.get("reason", "")), "Sale rejected.")
        else:
            messages.error(request, "Unknown action.")
        return redirect("assetfund:staff_detail", pk=app.pk)

    return render(request, "assetfund/staff_detail.html", {
        "app": app, "stages": app.stages.all(), "sales": app.sales.all(), "cap": svc.RATE_CAPS.get(app.kind),
        "rows": range(1, svc.MAX_STAGES + 1), "next_order": svc._next_order(app)})
