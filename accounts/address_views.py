import logging
import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from backoffice.audit import log as audit_log

from .address_forms import AddressForm
from .address_services import review_address, submit_address
from .models import Account, AddressProof
from .utils import throttled

log = logging.getLogger("kyc.audit")


def _staff(user):
    if not (user.is_active and user.is_staff and user.has_perm("accounts.review_addressproof")):
        raise PermissionDenied


@login_required
def address_view(request):
    account = get_object_or_404(Account, user=request.user)
    latest = AddressProof.objects.filter(user=request.user).first()          # newest first
    blocked = latest is not None and latest.status in ("pending", "approved")
    can_submit = not blocked and not account.closed_at
    form = AddressForm(request.POST or None, request.FILES or None)

    if request.method == "POST" and can_submit:
        if throttled(request, "address", 5):
            form.add_error(None, "Too many attempts. Try again later.")
        elif form.is_valid():
            d = form.cleaned_data
            try:
                submit_address(request.user, line1=d["line1"], line2=d["line2"], city=d["city"],
                               state=d["state"], bill_date=d["bill_date"], document=d["document"])
            except ValidationError as e:
                form.add_error(None, e.messages[0])
            else:
                messages.success(request, "Thanks. We received your address and will review it soon.")
                return redirect("accounts:address")
    return render(request, "accounts/address.html", {"form": form, "latest": latest, "can_submit": can_submit})


@login_required
def address_queue(request):
    _staff(request.user)
    proofs = (AddressProof.objects.filter(status="pending").select_related("user")
              .order_by("submitted_at")[:100])
    return render(request, "accounts/address_staff.html", {"proofs": proofs})


@login_required
def address_review(request, pk):
    _staff(request.user)
    proof = get_object_or_404(AddressProof.objects.select_related("user"), pk=pk)
    if request.method == "POST":
        decision = request.POST.get("decision", "")
        reason = request.POST.get("reason", "")
        try:
            review_address(proof.pk, request.user, decision, reason)
        except ValidationError as e:
            messages.error(request, e.messages[0])
        except PermissionDenied as e:
            messages.error(request, str(e) or "Not allowed.")
        else:
            audit_log(request.user, "address_" + decision, f"address:{proof.pk}", reason)
            messages.success(request, "Decision saved.")
            return redirect("accounts:address_staff")
        return redirect("accounts:address_review", pk=proof.pk)
    own = proof.user_id == request.user.pk
    return render(request, "accounts/address_review.html", {
        "proof": proof, "own": own, "can_decide": proof.status == "pending" and not own})


@login_required
def address_document(request, pk):
    _staff(request.user)
    proof = get_object_or_404(AddressProof, pk=pk)
    if not proof.document:
        raise Http404
    log.info("Address document %s opened by user %s", proof.pk, request.user.pk)
    ext = os.path.splitext(proof.document.name)[1].lower()
    resp = FileResponse(proof.document.open("rb"), as_attachment=True, filename=f"address-{proof.pk}{ext}")
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Cache-Control"] = "no-store"
    return resp
