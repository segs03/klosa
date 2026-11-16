import logging
import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from .kyc_forms import KYCForm
from .kyc_services import age_on, duplicate_hints, review_application, submit_application, verified_types
from .models import Account, KYCApplication, VerifiedIdentity, WelcomeBonus

from .utils import throttled
from django.utils import timezone
from backoffice.audit import log as audit_log

from django.conf import settings
from .bonus import bonus_amount




log = logging.getLogger("kyc.audit")


def _staff(user):
    if not (user.is_active and user.is_staff and user.has_perm("accounts.review_kycapplication")):
        raise PermissionDenied


@login_required
def verify_view(request):
    account = get_object_or_404(Account, user=request.user)
    types = verified_types(request.user)
    missing = [t for t in ("nin", "bvn") if t not in types]
    apps = {a.document_type: a for a in KYCApplication.objects.filter(user=request.user).order_by("submitted_at")}
    pending = next((a for a in apps.values() if a.status in ("pending", "under_review")), None)
    # the customer can always prepare the next ID while one is being reviewed
    next_type = missing[0] if missing else None
    can_submit = bool(missing) and not account.closed_at and (pending is None or pending.document_type != next_type)
    if pending and missing and pending.document_type == missing[0] and len(missing) > 1:
        next_type = missing[1]
        can_submit = True
    form = KYCForm(request.POST or None, request.FILES or None, initial={"doc_type": next_type or "nin"})

    if request.method == "POST" and can_submit:
        if throttled(request, "kyc", 10):
            form.add_error(None, "Too many attempts. Try again later.")
        elif form.is_valid():
            d = form.cleaned_data
            try:
                submit_application(request.user, doc_type=d["doc_type"], id_number=d["id_number"],
                                   legal_name=d["legal_name"], date_of_birth=d["date_of_birth"],
                                   document=d["document"])
            except ValidationError as e:
                form.add_error(None, e.messages[0])
            else:
                messages.success(request, "Thanks. We received your ID and will review it soon.")
                return redirect("accounts:verify")

    steps = []
    for code, label in (("nin", "National ID (NIN)"), ("bvn", "Bank verification (BVN)")):
        a = apps.get(code)
        state = ("done" if code in types else "review" if a and a.status in ("pending", "under_review")
                 else "more" if a and a.status == "additional_info" else "rejected" if a and a.status == "rejected" else "todo")
        note = a.reviews.order_by("-reviewed_at").values_list("reason", flat=True).first() if a and state in ("more", "rejected") else ""
        steps.append({"code": code, "label": label, "state": state, "note": note})

    given = WelcomeBonus.objects.filter(user=request.user).first()
    return render(request, "accounts/verify.html", {
        "form": form, "account": account, "steps": steps, "can_submit": can_submit, "both": not missing,
        "next_type": next_type, "bonus_amount": bonus_amount() if not given else None, "bonus_given": given})


@login_required
def staff_review(request, pk):
    _staff(request.user)
    app = get_object_or_404(KYCApplication.objects.select_related("user"), pk=pk)
    if request.method == "POST":
        try:
            review_application(app.pk, request.user, request.POST.get("decision", ""), request.POST.get("reason", ""))
        except ValidationError as e:
            messages.error(request, e.messages[0])
        except PermissionDenied as e:
            messages.error(request, str(e) or "Not allowed.")
        else:
            audit_log(request.user, "kyc_" + request.POST.get("decision", ""), f"kyc:{app.pk}", request.POST.get("reason", ""))
            messages.success(request, "Decision saved. The customer has been told.")
            return redirect("accounts:kyc_staff")
        return redirect("accounts:kyc_review", pk=app.pk)
    reviews = app.reviews.select_related("reviewer")
    own = app.user_id == request.user.pk
    already = reviews.filter(reviewer=request.user).exists()
    waiting = app.status in ("pending", "under_review")
    why = ""
    if not waiting:
        why = f"This request is {app.get_status_display().lower()}."
    elif own:
        why = "This is your own ID request. A colleague has to review it."
    elif already:
        why = "You already gave your decision. Another reviewer must give the next one."
    return render(request, "accounts/kyc_review.html", {
        "app": app, "age": age_on(app.date_of_birth, timezone.localdate()), "hints": duplicate_hints(app),
        "reviews": reviews, "can_decide": waiting and not own and not already, "why": why})
        
        

@login_required
def staff_list(request):
    _staff(request.user)
    apps = (KYCApplication.objects.filter(status__in=("pending", "under_review"))
            .select_related("user").order_by("submitted_at")[:100])
    return render(request, "accounts/kyc_staff.html", {"apps": apps})


#@login_required
#def staff_review(request, pk):
#    _staff(request.user)
#    app = get_object_or_404(KYCApplication.objects.select_related("user"), pk=pk)
#    if request.method == "POST":
#        try:
#            review_application(app.pk, request.user, request.POST.get("decision", ""), request.POST.get("reason", ""))
#        except ValidationError as e:
#            messages.error(request, e.messages[0])
#        except PermissionDenied as e:
#            messages.error(request, str(e) or "Not allowed.")
#        else:
#            messages.success(request, "Decision saved.")
#            audit_log(request.user, "kyc_" + request.POST.get("decision", ""), f"kyc:{app.pk}", request.POST.get("reason", ""))
#            return redirect("accounts:kyc_staff")
#        return redirect("accounts:kyc_review", pk=app.pk)
#    reviews = app.reviews.select_related("reviewer")
#    return render(request, "accounts/kyc_review.html", {
#        "app": app, "age": age_on(app.date_of_birth, timezone.localdate()), "hints": duplicate_hints(app),
#        "reviews": reviews,
#        "can_decide": app.status in ("pending", "under_review") and app.user_id != request.user.pk
#                      and not reviews.filter(reviewer=request.user).exists()})


@login_required
def staff_document(request, pk):
    _staff(request.user)
    app = get_object_or_404(KYCApplication, pk=pk)
    if not app.identity_document:
        raise Http404
    log.info("KYC document %s opened by user %s", app.pk, request.user.pk)
    ext = os.path.splitext(app.identity_document.name)[1].lower()
    resp = FileResponse(app.identity_document.open("rb"), as_attachment=True, filename=f"kyc-{app.pk}{ext}")
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Cache-Control"] = "no-store"
    return resp