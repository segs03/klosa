from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import KYCApplication
from accounts.utils import hit
from backoffice.audit import log as audit_log
from . import service as fa
from .models import FaceConsent, FaceFrame, FaceSession
from django.conf import settings



User = get_user_model()
PENDING_TTL = 600


def _actor(request):
    if request.user.is_authenticated:
        return request.user, None
    p = request.session.get("face_pending")
    if p and timezone.now().timestamp() - p["ts"] < PENDING_TTL:
        return User.objects.filter(pk=p["uid"], is_active=True).first(), p
    return None, None


def begin(request, purpose=None):
    user, pending = _actor(request)
    if not user:
        return redirect("accounts:login")
    purpose = pending["purpose"] if pending else (purpose or "upgrade")
    if purpose not in ("signup", "upgrade") and not pending:
        raise PermissionDenied
    if request.method == "POST":
        if not request.POST.get("consent"):
            messages.error(request, "Please agree so we can check your face.")
        else:
            FaceConsent.objects.get_or_create(user=user, version=settings.FACE_CONSENT_VERSION)
            try:
                s, nonce = fa.start(user, purpose)
            except ValidationError as e:
                messages.error(request, e.messages[0])
            else:
                request.session["face"] = {"id": s.pk, "nonce": nonce}
                return redirect("face_auth:capture")
    return render(request, "face_auth/start.html", {"purpose": purpose})


def capture(request):
    user, _ = _actor(request)
    s = request.session.get("face") or {}
    sess = user and FaceSession.objects.filter(pk=s.get("id"), user=user, status="capturing",
                                               expires_at__gt=timezone.now()).first()
    if not sess:
        messages.error(request, "Start a new face check.")
        return redirect("accounts:login" if not user else "face_auth:begin")
    return render(request, "face_auth/capture.html", {"labels": sess.prompt_labels()})


@require_POST
def frame(request):
    user, _ = _actor(request)
    s = request.session.get("face") or {}
    f = request.FILES.get("image")
    if not user:
        return JsonResponse({"error": "Not allowed."}, status=403)
    if hit(f"face:{user.pk}", 40, 3600):
        return JsonResponse({"error": "Too many attempts. Try again later."}, status=429)
    try:
        if not s or f is None:
            raise ValidationError("Start a new face check.")
        done = fa.add_frame(user, s["id"], s["nonce"], int(request.POST.get("step", "-1")), f.read(700 * 1024))
    except (ValidationError, ValueError) as e:
        return JsonResponse({"error": e.messages[0] if isinstance(e, ValidationError) else "Bad request."}, status=400)
    except (PermissionDenied, FaceSession.DoesNotExist):
        return JsonResponse({"error": "Not allowed."}, status=403)
    return JsonResponse({"ok": True, "done": done, "next": reverse("face_auth:result")})


def result(request):
    user, pending = _actor(request)
    s = request.session.get("face") or {}
    sess = user and FaceSession.objects.filter(pk=s.get("id"), user=user).first()
    if not sess:
        return redirect("accounts:login")
    if sess.status == "passed" and pending and fa.consume(user, sess.purpose):
        login(request, user, backend=pending["backend"])
        request.session.pop("face_pending", None)
        request.session.pop("face", None)
        return redirect("accounts:home")
    return render(request, "face_auth/result.html", {"session": sess})
    
    
def _staff(user):
    if not (user.is_active and user.is_staff and user.has_perm("face_auth.review_facesession")):
        raise PermissionDenied


@login_required
def queue(request):
    _staff(request.user)
    checks = FaceSession.objects.filter(status="review").select_related("user").order_by("created_at")[:100]
    return render(request, "face_auth/queue.html", {"checks": checks})


@login_required
def review(request, pk):
    _staff(request.user)
    s = get_object_or_404(FaceSession.objects.select_related("user"), pk=pk)
    if request.method == "POST":
        try:
            fa.decide(s.pk, request.user, request.POST.get("decision", ""), request.POST.get("reason", ""))
        except ValidationError as e:
            messages.error(request, e.messages[0])
        except PermissionDenied as e:
            messages.error(request, str(e) or "Not allowed.")
        else:
            messages.success(request, "Decision saved.")
            return redirect("face_auth:queue")
        return redirect("face_auth:review", pk=s.pk)
    doc = (KYCApplication.objects.filter(user=s.user).exclude(identity_document="")
           .order_by("-submitted_at").first())
    return render(request, "face_auth/review.html", {
        "check": s, "frames": s.frames.all(), "labels": s.prompt_labels(), "doc": doc,
        "can_decide": s.status == "review" and s.user_id != request.user.pk})


@login_required
def image(request, pk, step):
    _staff(request.user)
    frame = get_object_or_404(FaceFrame, session_id=pk, step=step)
    if not frame.path:
        raise Http404
    audit_log(request.user, "face_image_opened", f"face:{pk}", f"step {step}")
    resp = FileResponse(fa.storage.open(frame.path, "rb"), content_type="image/jpeg")
    resp["Cache-Control"] = "no-store"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp