import hashlib
import ipaddress
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError

from django.utils import timezone

from accounts.storage import PrivateStorage
from backoffice.models import SecurityEvent
from . import engine, vault
from .models import FaceDuplicate, FaceEnrollment, FaceFrame, FaceSession

from accounts.kyc_services import verified_types

import io
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from backoffice.audit import log as audit_log




TEXT = {"no_face": "We could not see your face clearly.",
        "many_faces": "More than one face was in the photo.",
        "unreadable": "A photo could not be read.",
        "prompt": "We could not see you follow the prompts."}
DOWN = "Face check is temporarily unavailable. Try again later."


log = logging.getLogger("face_auth")
MODES = {"signup": "enroll", "login": "auto", "ip_change": "auto", "password_reset": "auto",
         "phone_change": "auto", "recovery": "review", "upgrade": "review"}
STEPS, WINDOW, FRAMES_MATCH, PASS_VALID, DAILY_LIMIT = 3, timedelta(minutes=5), 0.30, timedelta(minutes=15), 6
        
        

       
try:
    from PIL import Image, ImageChops, ImageStat
except ImportError:
    Image = None

MIN_GAP, MIN_BYTES, MAX_BYTES, MIN_SIDE, MIN_DIFF = 1.0, 1000, 600 * 1024, 240, 4.0
storage = PrivateStorage()


def _thumb(img):
    return img.convert("L").resize((64, 64))



def _h(text):
    return hashlib.sha256((text or "").encode()).hexdigest()


def _read_frames(session):
    storage, blobs = PrivateStorage(), []
    for f in session.frames.order_by("step"):
        with storage.open(f.path, "rb") as fh:
            blobs.append(fh.read())
    return blobs


def network_id(ip):
    try:
        a = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return None
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return str(ipaddress.ip_network((a, 24 if a.version == 4 else 64), strict=False))


def new_network(user, request):
    here = network_id(request.META.get("REMOTE_ADDR", "")) if request else None
    seen = SecurityEvent.objects.filter(user=user, event="login").exclude(ip=None) \
        .order_by().values_list("ip", flat=True).distinct()[:200]
    nets = {network_id(i) for i in seen} - {None}
    return bool(here and nets and here not in nets)    # no history, no check


def bonus_ok(user):
    """For grant_welcome_bonus: an enrolled face that no other open account shares."""
    return (FaceEnrollment.objects.filter(user=user).exists()
            and not FaceDuplicate.objects.filter(user=user, status__in=("open", "confirmed")).exists())


def _flag_duplicates(user, vec):
    for e in (FaceEnrollment.objects.exclude(user=user)
              .filter(user__account__closed_at__isnull=True).iterator()):
        score = engine.similarity(vault.unseal(e.template_enc), vec)
        if score >= settings.FACE_DUPLICATE_THRESHOLD:
            FaceDuplicate.objects.update_or_create(user=user, other_user_id=e.user_id, defaults={"score": score})


def _welcome(user_id):
    try:
        from accounts.bonus import grant_welcome_bonus
        from django.contrib.auth import get_user_model
        grant_welcome_bonus(get_user_model().objects.get(pk=user_id))
    except Exception:
        log.exception("welcome gift failed for %s", user_id)


def required(user, purpose, request=None):
    """Whether this action needs a face check right now. Nothing is forced until the purpose is listed in FACE_ENFORCE."""
    if purpose not in settings.FACE_ENFORCE:
        return False
    enrolled = FaceEnrollment.objects.filter(user=user).first()
    if purpose == "login":
        return bool(enrolled and enrolled.use_at_login)
    if purpose == "ip_change":
        return bool(enrolled) and new_network(user, request)
    return True                                        # signup, upgrade, recovery, password_reset, phone_change


def start(user, purpose, bound_to=""):
    if purpose not in MODES:
        raise ValidationError("Unknown face check.")
    now = timezone.now()
    if purpose == "upgrade" and not (verified_types(user) & {"nin", "bvn"}):
        raise ValidationError("Verify your NIN or BVN first.")
    with transaction.atomic():
        if FaceSession.objects.filter(user=user, created_at__gte=now - timedelta(days=1)).count() >= DAILY_LIMIT:
            raise ValidationError("Too many face checks today. Try again tomorrow.")
        FaceSession.objects.filter(user=user, status="capturing").update(status="expired")
        nonce = secrets.token_urlsafe(24)
        prompts = secrets.SystemRandom().sample(list(FaceSession.LABELS), STEPS)
        s = FaceSession.objects.create(user=user, purpose=purpose, bound_hash=_h(bound_to), prompts=prompts,
                                       nonce_hash=_h(nonce), expires_at=now + WINDOW)
    return s, nonce


def add_frame(user, session_pk, nonce, step, data):
    """Returns True when the last frame was accepted and the session has been decided."""
    if Image is None:
        raise ValidationError("Face checks are not available on this server.")
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
        s = FaceSession.objects.select_for_update().get(pk=session_pk, user=user)
        if not secrets.compare_digest(s.nonce_hash, _h(nonce)):
            raise PermissionDenied
        frames = list(s.frames.order_by("step"))
        if not 0 <= step < STEPS:
            raise ValidationError("That step is out of order. Start again.")
        if step < len(frames):                      # browser retry of the same request
            if frames[step].sha256 == sha:
                return step + 1 == STEPS
            raise ValidationError("That step was already captured. Start again.")
        if s.status != "capturing" or s.expires_at < now:
            raise ValidationError("This check expired. Start again.")
        if step != len(frames):
            raise ValidationError("That step is out of order. Start again.")
        if frames:
            last = frames[-1]
            if (now - last.taken_at).total_seconds() < MIN_GAP:
                raise ValidationError("Too fast. Do the movement, then capture.")
            with storage.open(last.path, "rb") as fh:
                prev = _thumb(Image.open(io.BytesIO(fh.read())))
            if ImageStat.Stat(ImageChops.difference(prev, thumb)).mean[0] < MIN_DIFF:
                raise ValidationError("The photos look the same. Follow the prompt each time.")
        name = storage.save(f"face/{s.pk}-{step}-{secrets.token_hex(6)}.jpg", ContentFile(data))
        try:
            with transaction.atomic():
                FaceFrame.objects.create(session=s, step=step, path=name, sha256=sha)
        except IntegrityError:
            storage.delete(name)
            raise ValidationError("This photo was already used. Take a new one.")
    done = step + 1 == STEPS
    if done:
        finish(s.pk)                                # outside the lock above
    return done


@transaction.atomic
def finish(pk):
    s = FaceSession.objects.select_for_update().select_related("user").get(pk=pk)
    if s.status != "capturing":
        return s
    enrolled = FaceEnrollment.objects.filter(user=s.user).first()
    mode = MODES[s.purpose]
    if mode == "auto" and enrolled is None:
        mode = "review"                                    # older account with no template: a person checks

    def close(status, by, score=None, reason=""):
        s.status, s.decided_by, s.score, s.reason, s.decided_at = status, by, score, reason[:300], timezone.now()
        s.save(update_fields=["status", "decided_by", "score", "reason", "decided_at"])
        return s

    def unavailable():
        return close("review", "none") if mode == "review" else close("failed", "none", reason=DOWN)

    if not engine.available():
        return unavailable()
    try:
        vec, low, code = engine.analyse(_read_frames(s), s.prompts)
    except Exception:
        log.exception("face engine failed for session %s", s.pk)
        return unavailable()
    if code != "ok":
        return close("failed", "auto", 0.0, TEXT[code] + " Try again in good light, alone.")
    if low < FRAMES_MATCH:
        return close("failed", "auto", round(low, 3), "The photos did not look like one person. Try again.")

    if mode == "enroll":
        if enrolled:
            return close("failed", "auto", reason="A face is already enrolled on this account.")
        FaceEnrollment.objects.create(user=s.user, template_enc=vault.seal(vec))
        _flag_duplicates(s.user, vec)
        transaction.on_commit(lambda: _welcome(s.user_id))
        return close("passed", "auto", round(low, 3))
    if enrolled is None:
        return close("review", "none")      # nothing to compare with: a person decides
    score = engine.similarity(vault.unseal(enrolled.template_enc), vec)
    if mode == "auto":
        ok = score >= settings.FACE_AUTO_THRESHOLD
        return close("passed" if ok else "failed", "auto", round(score, 3),
                     "" if ok else "This face does not match the one on your account.")
    return close("review", "none", round(score, 3))        # recovery, upgrade: staff compare with the ID



def satisfied(user, purpose, bound_to=""):
    return (FaceSession.objects.filter(user=user, purpose=purpose, status="passed", consumed_at__isnull=True,
                                       bound_hash=_h(bound_to), decided_at__gte=timezone.now() - PASS_VALID)
            .order_by("-decided_at").first())


def consume(user, purpose, bound_to=""):
    """Uses up a pass. Call it inside the same transaction as the action it protects."""
    s = satisfied(user, purpose, bound_to)
    return bool(s) and FaceSession.objects.filter(pk=s.pk, consumed_at__isnull=True).update(consumed_at=timezone.now()) == 1
    
    
@transaction.atomic
def decide(session_pk, reviewer, decision, reason=""):
    if not (reviewer and reviewer.is_authenticated and reviewer.is_active and reviewer.is_staff
            and reviewer.has_perm("face_auth.review_facesession")):
        raise PermissionDenied("You are not allowed to review face checks.")
    if decision not in ("passed", "failed"):
        raise ValidationError("Choose pass or fail.")
    reason = " ".join((reason or "").split())
    if decision == "failed" and not reason:
        raise ValidationError("Give a reason the customer can read.")
    s = FaceSession.objects.select_for_update().select_related("user").get(pk=session_pk)
    if s.status != "review":
        raise ValidationError("This check is not waiting for review.")
    if s.user_id == reviewer.pk:
        raise PermissionDenied("You cannot review your own face check.")
    s.status, s.reason, s.decided_by = decision, reason[:300], "staff"
    s.reviewed_by, s.decided_at = reviewer, timezone.now()
    s.save(update_fields=["status", "reason", "decided_by", "reviewed_by", "decided_at"])
    if decision == "passed" and s.purpose == "upgrade":
        from accounts.models import Account
        from accounts.tiers import refresh_tier
        Account.objects.filter(user=s.user).update(face_verified_at=s.decided_at)
        refresh_tier(s.user)
    audit_log(reviewer, f"face_{decision}", f"face:{s.pk}", reason)
    return s
