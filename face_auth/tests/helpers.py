"""Shared fixtures for the face_auth tests. Not a test module itself."""
import io
import random
from datetime import timedelta

import numpy as np
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.utils import timezone
from PIL import Image

from .. import service as fa, vault
from ..models import FaceEnrollment, FaceSession

User = get_user_model()
KEY = Fernet.generate_key().decode()
SETTINGS = dict(
    FACE_AUTH_KEY=KEY, FACE_AUTO_THRESHOLD=0.40, FACE_DUPLICATE_THRESHOLD=0.45,
    FACE_CONSENT_VERSION="1",
    FACE_ENFORCE={"signup", "login", "ip_change", "password_reset", "phone_change", "recovery", "upgrade"},
)




def jpeg(seed=1, size=300, quality=70):
    raw = random.Random(seed).randbytes(size * size * 3)
    buf = io.BytesIO()
    Image.frombytes("RGB", (size, size), raw).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def gradient(quality):
    buf = io.BytesIO()
    Image.linear_gradient("L").convert("RGB").save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def vec(*head):
    v = np.zeros(128, np.float32)
    v[:len(head)] = head
    return v.tobytes()


def make_user(name, **kw):
    return User.objects.create_user(username=name, password="pw-12345-x", **kw)


def make_account(user, closed=False):
    from accounts.models import Account
    acc, _ = Account.objects.get_or_create(
        user=user, defaults={"account_number": f"{user.pk:010d}"})
    if closed:
        Account.objects.filter(pk=acc.pk).update(closed_at=timezone.now())
    return acc


class FakeStorage:
    def __init__(self):
        self.files = {}

    def save(self, name, content):
        self.files[name] = content.read()
        return name

    def open(self, name, mode="rb"):
        return io.BytesIO(self.files[name])

    def delete(self, name):
        self.files.pop(name, None)


def enroll(user, vector=None):
    return FaceEnrollment.objects.create(user=user, template_enc=vault.seal(vector or vec(1)))


def session(user, purpose="login", **kw):
    now = timezone.now()
    d = dict(user=user, purpose=purpose, prompts=["smile", "left", "open"], nonce_hash="x",
             expires_at=now + timedelta(minutes=5))
    d.update(kw)
    return FaceSession.objects.create(**d)


def passed(user, purpose="login", bound="", age=timedelta(0)):
    return session(user, purpose, status="passed", bound_hash=fa._h(bound), decided_by="auto",
                   decided_at=timezone.now() - age)
