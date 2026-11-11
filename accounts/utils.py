
import hashlib
import hmac
import re
import secrets

from django.conf import settings
from django.core.cache import cache


def normalize_email(email):
    email = (email or "").strip().lower()
    if "@" not in email:
        return email
    local, domain = email.rsplit("@", 1)
    local = local.split("+", 1)[0]
    if domain in ("gmail.com", "googlemail.com"):
        local, domain = local.replace(".", ""), "gmail.com"
    return f"{local}@{domain}"


def new_code(n=6):
    return "".join(secrets.choice("0123456789") for _ in range(n))


def digest(*parts, key=None):
    k = (key or settings.SECRET_KEY).encode()
    return hmac.new(k, "|".join(map(str, parts)).encode(), hashlib.sha256).hexdigest()


def same(a, b):
    return hmac.compare_digest(a, b)


def normalize_phone(raw):
    """Nigerian mobile numbers only. Returns +234XXXXXXXXXX or None."""
    s = re.sub(r"[\s\-().]", "", raw or "")
    if s.startswith("+"):
        s = s[1:]
    if s.startswith("234"):
        s = s[3:]
    elif s.startswith("0"):
        s = s[1:]
    return "+234" + s if re.fullmatch(r"[789][01]\d{8}", s) else None


def hit(key, limit, seconds=3600):
    """Count one use of a limited action. True means the limit is passed."""
    cache.add(key, 0, seconds)
    try:
        return cache.incr(key) > limit
    except ValueError:
        cache.set(key, 1, seconds)
        return False


def throttled(request, name, limit, seconds=3600):
    return hit(f"th:{name}:{request.META.get('REMOTE_ADDR', '')}", limit, seconds)