import hashlib
import io
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from backoffice.audit import log as audit_log
from .kyc_services import verified_types
from .models import Account, FaceCheck, FaceFrame
from .storage import PrivateStorage

try:
    from PIL import Image, ImageChops, ImageStat
except ImportError:                                     # the page tells the user the feature is unavailable
    Image = None

log = logging.getLogger(__name__)
STEPS, WINDOW = 3, timedelta(minutes=5)
MIN_GAP, MIN_BYTES, MAX_BYTES, MIN_SIDE, MIN_DIFF = 1.0, 1000, 600 * 1024, 240, 4.0
storage = PrivateStorage()


class ManualReview:
    """The real default: no automatic decision. A person compares the frames with the ID."""

    def assess(self, check):
        return "review", None, ""


def _h(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _thumb(img):
    return img.convert("L").resize((64, 64))


def start_check(user):
    if Image is None:
        raise ValidationError("Face checks are not available on this server.")
    acc = Account.objects.filter(user=user).first()
    if acc is None or acc.closed_at:
        raise ValidationError("Your account is not open.")
    if not (verified_types(user) & {"nin", "bvn"}):
        raise ValidationError("Verify your NIN or BVN first.")
    if acc.face_verified_at:
        raise ValidationError("Your face is already verified.")
    now = timezone.now()
    with transaction.atomic():
        Account.objects.select_for_update().get(pk=acc.pk)
        if FaceCheck.objects.filter(user=user, status="review").exists():
            raise ValidationError("Your face check is already being reviewed.")
        if FaceCheck.objects.filter(user=user, created_at__gte=now - timedelta(days=1)).count() >= 3:
            raise ValidationError("You have reached today's limit of 3 attempts. Try again tomorrow.")
        FaceCheck.objects.filter(user=user, status="capturing").update(status="expired")
        nonce = secrets.token_urlsafe(24)
        prompts = secrets.SystemRandom().sample(list(FaceCheck.LABELS), STEPS)
        check = FaceCheck.objects.create(user=user, prompts=prompts, nonce_hash=_h(nonce), expires_at=now + WINDOW)
    return check, nonce


def add_frame(user, check_pk, nonce, step, data):
    """Returns True when the last frame was accepted."""
    if not MIN_BYTES <= len(data) <= MAX_BYTES or not data.startswith(b"\xff\xd8\xff"):
        raise ValidationError("That photo is not usable. Use the camera and try again.")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise ValidationError("That photo could not be read.")
    if img.format != "JPEG" or min(img.size) < MIN_SIDE or max(img.size) > 2000:
        raise ValidationError("The photo is too small. Hold the phone steady and try again.")
    thumb = _thumb(img)
    if ImageStat.Stat(thumb).stddev[0] < 8:
        raise ValidationError("The photo is too dark or blank. Find better light.")
    sha, now = hashlib.sha256(data).hexdigest(), timezone.now()

    with transaction.atomic():
        check = FaceCheck.objects.select_for_update().get(pk=check_pk, user=user)
        if not secrets.compare_digest(check.nonce_hash, _h(nonce or "")):
            raise PermissionDenied
        frames = list(check.frames.order_by("step"))
        if not 0 <= step < STEPS:
            raise ValidationError("That step is out of order. Start again.")
        if step < len(frames):                          # a retry: the browser sent the same request twice
            if frames[step].sha256 == sha:
                return step + 1 == STEPS
            raise ValidationError("That step was already captured. Start again.")
        if check.status != "capturing" or check.expires_at < now:
            raise ValidationError("This check expired. Start again.")
        if step != len(frames):
            raise ValidationError("That step is out of order. Start again.")
        if frames:
            last = frames[-1]
            if (now - last.taken_at).total_seconds() < MIN_GAP:
                raise ValidationError("Too fast. Do the movement, then capture.")
            if ImageStat.Stat(ImageChops.difference(_thumb(Image.open(storage.path(last.path))), thumb)).mean[0] < MIN_DIFF:
                raise ValidationError("The photos look the same. Follow the prompt each time.")
        name = storage.save(f"face/{check.pk}-{step}-{secrets.token_hex(6)}.jpg", ContentFile(data))
        try:
            with transaction.atomic():
                FaceFrame.objects.create(face_check=check, step=step, path=name, sha256=sha)
        except IntegrityError:
            storage.delete(name)
            raise ValidationError("This photo was already used. Take a new one.")
        done = step + 1 == STEPS
        if done:
            res = import_string(settings.FACE_PROVIDER)().assess(check)
            status, score, ref = res[:3]
            check.reason = res[3] if len(res) > 3 else ""
            check.status = status if status in ("review", "passed", "failed") else "review"
            check.score, check.provider_ref = score, (ref or "")[:100]
            check.save(update_fields=["status", "score", "provider_ref", "reason"])
            if check.status == "passed":
                Account.objects.filter(user=user).update(face_verified_at=now)
                from .tiers import refresh_tier
                refresh_tier(user)            # in add_frame, inside the "passed" block
    return done


@transaction.atomic
def decide(check_pk, reviewer, decision, reason=""):
    if not (reviewer and reviewer.is_authenticated and reviewer.is_active and reviewer.is_staff
            and reviewer.has_perm("accounts.review_facecheck")):
        raise PermissionDenied("You are not allowed to review face checks.")
    if decision not in ("passed", "failed"):
        raise ValidationError("Choose pass or fail.")
    reason = " ".join((reason or "").split())
    if decision == "failed" and not reason:
        raise ValidationError("Give a reason the customer can read.")
    check = FaceCheck.objects.select_for_update().select_related("user").get(pk=check_pk)
    if check.status != "review":
        raise ValidationError("This check is not waiting for review.")
    if check.user_id == reviewer.pk:
        raise PermissionDenied("You cannot review your own face check.")
    check.status, check.reason = decision, reason[:300]
    check.reviewed_by, check.reviewed_at = reviewer, timezone.now()
    check.save(update_fields=["status", "reason", "reviewed_by", "reviewed_at"])
    if decision == "passed":
        Account.objects.filter(user=check.user).update(face_verified_at=timezone.now())
        from .tiers import refresh_tier
        refresh_tier(check.user)
    audit_log(reviewer, f"face_{decision}", f"face:{check.pk}", reason)
    return check