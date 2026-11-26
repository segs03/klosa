import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings


def _fernet():
    key = getattr(settings, "FACE_AUTH_KEY", "")
    if key:
        return Fernet(key.encode())
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("face:" + settings.SECRET_KEY).encode()).digest()))


def seal(raw):
    return _fernet().encrypt(raw)


def unseal(token):
    return _fernet().decrypt(bytes(token))