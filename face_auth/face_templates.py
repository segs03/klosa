"""Face templates: stop one face opening many accounts.

After a face check passes, we turn the photos into one numeric template, encrypt it, and compare it with
every other open account's template. A close match is flagged for staff (FaceDuplicate); the welcome gift
waits until a flagged account is cleared. Templates are deleted when the account closes. No photo is kept here.
Needs FACE_TEMPLATE_KEY (a Fernet key).
"""
import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from . import face_math
from .models import FaceCheck, FaceDuplicate, FaceTemplate

log = logging.getLogger("kyc.audit")
OPEN = ("open", "confirmed")


def enabled():
    return bool(getattr(settings, "FACE_TEMPLATE_KEY", ""))


def _fernet():
    from cryptography.fernet import Fernet
    key = getattr(settings, "FACE_TEMPLATE_KEY", "")
    if not key:
        raise ImproperlyConfigured("FACE_TEMPLATE_KEY is not set.")
    return Fernet(key.encode() if isinstance(key, str) else key)


def to_vector(embedding):
    """OpenCV gives a 1x128 array; tests give plain lists."""
    if hasattr(embedding, "ravel"):
        return [float(x) for x in embedding.ravel().tolist()]
    return [float(x) for x in embedding]


def enroll(user, vectors, check=None):
    vec = face_math.normalize(face_math.mean_vector(vectors))
    data = _fernet().encrypt(face_math.pack(vec))
    FaceTemplate.objects.update_or_create(user=user, defaults={"data": data, "dims": len(vec), "check": check})
    return vec


def load(template):
    return face_math.unpack(_fernet().decrypt(bytes(template.data)))


def scan(user, vector, threshold=None):
    """[(other_user_id, similarity)] for open accounts whose face looks like this one, best first."""
    threshold = float(getattr(settings, "FACE_DUPLICATE_THRESHOLD", 0.45)) if threshold is None else threshold
    others = (FaceTemplate.objects.exclude(user=user).filter(user__account__closed_at__isnull=True))
    hits = []
    for t in others.iterator():
        if t.dims != len(vector):
            continue
        score = face_math.cosine(vector, load(t))
        if score >= threshold:
            hits.append((t.user_id, score))
    return sorted(hits, key=lambda h: -h[1])


def record_duplicates(user, matches):
    for other_id, score in matches:
        flag, made = FaceDuplicate.objects.get_or_create(user=user, other_user_id=other_id, defaults={"score": score})
        if not made and score > flag.score:
            FaceDuplicate.objects.filter(pk=flag.pk).update(score=score)


def delete_for_user(user_or_id):
    """Call when an account closes. (The post_save signal does it for .save(); a queryset .update() needs this call.)"""
    return FaceTemplate.objects.filter(user_id=getattr(user_or_id, "pk", user_or_id)).delete()[0]


def unique_face_ok(user):
    return not FaceDuplicate.objects.filter(user=user, status__in=OPEN).exists()


def face_ok_for_bonus(user):
    """The gift needs a passed face check, and a face that no other open account already has."""
    return FaceCheck.objects.filter(user=user, status="passed").exists() and unique_face_ok(user)


def embeddings_for_check(check, analyzer=None):
    """One vector per usable photo of this check, read back from private storage."""
    from .face_klosa import CvAnalyzer, _read_private
    analyzer = analyzer or CvAnalyzer()
    vectors = []
    for frame in check.frames.order_by("step"):
        data = _read_private(frame.path)
        if not data:
            continue
        obs = analyzer.observe(data)
        if obs.get("faces") == 1 and obs.get("embedding") is not None:
            vectors.append(to_vector(obs["embedding"]))
    return vectors
