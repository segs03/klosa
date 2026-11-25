import logging
import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from backoffice.audit import log as audit_log

from .bonus import bonus_amount
from .bvn_services import verify_bvn
from .kyc_forms import BVNForm, KYCForm
from .kyc_services import (DOC_TYPES, age_on, duplicate_hints, review_application,
                           submit_application, verified_types)
from .models import Account, AddressProof, KYCApplication, WelcomeBonus
from .tiers import SECOND_IDS
from .utils import throttled

log = logging.getLogger("kyc.audit")

SECOND_ID_ORDER = ("nin", "passport", "drivers_license")


def _staff(user):
    if not (user.is_active and user.is_staff and user.has_perm("accounts.review_kycapplication")):
        raise PermissionDenied


def _state(done, app):
    if done:
        return "done"
    if app is None:
        return "todo"
    return {"pending": "review", "under_review": "review", "additional_info": "more",
            "rejected": "rejected"}.get(app.status, "todo")


def _note(app, state):
    if app and state in ("more", "rejected"):
        return app.reviews.order_by("-reviewed_at").values_list("reason", flat=True).first() or ""
    return ""


@login_required
def verify_view(request):
    account = get_object_or_404(Account, user=request.user)
    types = verified_types(request.user)
    apps = {a.document_type: a for a in KYCApplication.objects.filter(user=request.user).order_by("submitted_at")}

    # BVN is compulsory and checked from the number alone. One more ID (photo) is also needed.
    second_done = bool(types & SECOND_IDS)
    second_pending = any(a.status in ("pending", "under_review") for t, a in apps.items() if t in SECOND_IDS)
    options = []
    if "bvn" not in types:
        options.append("bvn")
    if not second_done and not second_pending:
        options += list(SECOND_ID_ORDER)
    can_submit = bool(options) and not account.closed_at
    next_type = options[0] if options else None

    bvn_result = request.session.pop("bvn_result", None)      # shown once, right after a successful check
    is_bvn = request.method == "POST" and request.POST.get("doc_type") == "bvn"
    bvn_form = BVNForm(request.POST if is_bvn else None)

    if is_bvn and can_submit and "bvn" in options:
        if throttled(request, "bvn", 5):                       # every lookup costs money
            bvn_form.add_error(None, "Too many attempts. Try again later.")
        elif bvn_form.is_valid():
            try:
                details = verify_bvn(request.user, bvn_form.cleaned_data["id_number"])
            except ValidationError as e:
                bvn_form.add_error(None, e.messages[0])
            else:
                request.session["bvn_result"] = details
                return redirect("accounts:verify")

    form = KYCForm(None if is_bvn else (request.POST or None),
                   None if is_bvn else (request.FILES or None),
                   initial={"doc_type": next_type or "nin"})

    if request.method == "POST" and can_submit and not is_bvn:
        if throttled(request, "kyc", 10):
            form.add_error(None, "Too many attempts. Try again later.")
        elif form.is_valid():
            d = form.cleaned_data
            if d["doc_type"] not in options:
                form.add_error("doc_type", "You can't submit this ID right now.")
            else:
                try:
                    submit_application(request.user, doc_type=d["doc_type"], id_number=d["id_number"],
                                       legal_name=d["legal_name"], date_of_birth=d["date_of_birth"],
                                       document=d["document"])
                except ValidationError as e:
                    form.add_error(None, e.messages[0])
                else:
                    messages.success(request, "Thanks. We received your ID and will review it soon.")
                    return redirect("accounts:verify")

    selected_type = "bvn" if is_bvn else (form["doc_type"].value() or "")

    bvn_app = apps.get("bvn")
    second_apps = [a for t, a in apps.items() if t in SECOND_IDS]
    second_app = max(second_apps, key=lambda a: a.submitted_at) if second_apps else None
    address = AddressProof.objects.filter(user=request.user).first()
    addr_state = {"approved": "done", "pending": "review", "rejected": "rejected"}.get(
        address.status if address else "", "todo")

    s1 = _state("bvn" in types, bvn_app)
    s2 = _state(second_done, second_app)
    steps = [
        {"code": "bvn", "label": "Bank verification (BVN)", "state": s1, "note": _note(bvn_app, s1)},
        {"code": "id", "label": "One more ID (NIN, passport or licence)", "state": s2, "note": _note(second_app, s2)},
        {"code": "address", "label": "Proof of address", "state": addr_state, "url": reverse("accounts:address"),
         "note": address.reason if address and addr_state == "rejected" else ""},
    ]

    given = WelcomeBonus.objects.filter(user=request.user).first()
    return render(request, "accounts/verify.html", {
        "form": form, "bvn_form": bvn_form, "bvn_result": bvn_result, "selected_type": selected_type,
        "account": account, "steps": steps, "can_submit": can_submit,
        "both": "bvn" in types and second_done,
        "type_options": [(t, DOC_TYPES[t][0]) for t in options],
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
