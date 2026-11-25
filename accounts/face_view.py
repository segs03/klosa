from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from backoffice.audit import log as audit_log
from .face import add_frame, decide, start_check, storage
from .models import Account, FaceCheck, FaceFrame, KYCApplication
from .utils import hit


def _staff(user):
    if not (user.is_active and user.is_staff and user.has_perm("accounts.review_facecheck")):
        raise PermissionDenied


@login_required
def face_start(request):
    acc = get_object_or_404(Account, user=request.user)
    if request.method == "POST":
        if not request.POST.get("consent"):
            messages.error(request, "Please agree so we can check your face.")
        else:
            try:
                check, nonce = start_check(request.user)
            except ValidationError as e:
                messages.error(request, e.messages[0])
            else:
                request.session["face"] = {"id": check.pk, "nonce": nonce}      # the nonce never reaches the page
                return redirect("accounts:face_capture")
    return render(request, "accounts/face_start.html", {
        "last": FaceCheck.objects.filter(user=request.user).first(), "done": bool(acc.face_verified_at)})


@login_required
def face_capture(request):
    s = request.session.get("face") or {}
    check = FaceCheck.objects.filter(pk=s.get("id"), user=request.user, status="capturing",
                                     expires_at__gt=timezone.now()).first()
    if not check:
        messages.error(request, "Start a new face check.")
        return redirect("accounts:face_start")
    return render(request, "accounts/face_capture.html", {"labels": check.prompt_labels()})


@login_required
@require_POST
def face_frame(request):
    s = request.session.get("face") or {}
    if hit(f"face:{request.user.pk}", 40, 3600):
        return JsonResponse({"error": "Too many attempts. Try again later."}, status=429)
    f = request.FILES.get("image")
    try:
        if not s or f is None:
            raise ValidationError("Start a new face check.")
        done = add_frame(request.user, s["id"], s["nonce"], int(request.POST.get("step", "-1")), f.read(700 * 1024))
    except (ValidationError, ValueError) as e:
        return JsonResponse({"error": e.messages[0] if isinstance(e, ValidationError) else "Bad request."}, status=400)
    except (PermissionDenied, FaceCheck.DoesNotExist):
        return JsonResponse({"error": "Not allowed."}, status=403)
    if done:
        request.session.pop("face", None)
    return JsonResponse({"ok": True, "done": done, "next": reverse("accounts:face_start")})


@login_required
def face_queue(request):
    _staff(request.user)
    checks = FaceCheck.objects.filter(status="review").select_related("user").order_by("created_at")[:100]
    return render(request, "accounts/face_queue.html", {"checks": checks})


@login_required
def face_review(request, pk):
    _staff(request.user)
    check = get_object_or_404(FaceCheck.objects.select_related("user"), pk=pk)
    if request.method == "POST":
        try:
            decide(check.pk, request.user, request.POST.get("decision", ""), request.POST.get("reason", ""))
        except ValidationError as e:
            messages.error(request, e.messages[0])
        except PermissionDenied as e:
            messages.error(request, str(e) or "Not allowed.")
        else:
            messages.success(request, "Decision saved. The customer has been told.")
            return redirect("accounts:face_queue")
        return redirect("accounts:face_review", pk=check.pk)
    doc = KYCApplication.objects.filter(user=check.user).exclude(identity_document="").order_by("-submitted_at").first()
    return render(request, "accounts/face_review.html", {
        "check": check, "frames": check.frames.all(), "labels": check.prompt_labels(), "doc": doc,
        "can_decide": check.status == "review" and check.user_id != request.user.pk})


@login_required
def face_image(request, pk, step):
    _staff(request.user)
    frame = get_object_or_404(FaceFrame, face_check_id=pk, step=step)
    if not frame.path:
        raise Http404
    audit_log(request.user, "face_image_opened", f"face:{pk}", f"step {step}")
    resp = FileResponse(storage.open(frame.path, "rb"), content_type="image/jpeg")
    resp["Cache-Control"] = "no-store"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp