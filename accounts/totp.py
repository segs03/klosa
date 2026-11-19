import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _fernet():
    configured = getattr(settings, "TOTP_ENCRYPTION_KEY", "")
    if configured:
        try:
            return Fernet(configured.encode())
        except (ValueError, TypeError):
            raise ImproperlyConfigured(
                "TOTP_ENCRYPTION_KEY is not a valid Fernet key. Make one with: "
                "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key)


def encrypt(secret):
    return _fernet().encrypt(secret.encode())


def decrypt(token):
    return _fernet().decrypt(token).decode()


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret, step):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    return f"{(struct.unpack('>I', mac[offset:offset + 4])[0] & 0x7FFFFFFF) % 10**6:06d}"


def verify(secret, code, last_step=0, now=None, window=1):
    """The matching 30-second step, or None. A step at or before last_step is refused, so a code works once."""
    code = (code or "").strip().replace(" ", "")
    if not (len(code) == 6 and code.isdigit()):
        return None
    now_step = int((time.time() if now is None else now) // 30)
    for step in range(now_step - window, now_step + window + 1):
        if step > last_step and hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def uri(secret, name, issuer="Klosa Bank"):
    return f"otpauth://totp/{quote(issuer)}:{quote(name)}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"