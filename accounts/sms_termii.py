"""Termii SMS sender. Plug in with:  SMS_BACKEND=accounts.sms_termii.send

deliver_sms() in accounts/services.py calls  import_string(SMS_BACKEND)(phone, text).
Settings it reads: TERMII_API_KEY, TERMII_BASE_URL, TERMII_SENDER_ID, TERMII_CHANNEL, TERMII_DAILY_CAP.
It raises on any failure; notify() logs it and the customer can ask for a new code.
"""
import json
import logging
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

log = logging.getLogger("sms")


def _setting(name, default=""):
    return getattr(settings, name, default) or default


def to_msisdn(phone):
    """Termii wants international digits with no plus: 2348012345678."""
    digits = re.sub(r"\D", "", str(phone or ""))
    if digits.startswith("0") and len(digits) == 11:
        return "234" + digits[1:]
    if len(digits) == 10:
        return "234" + digits
    if 11 <= len(digits) <= 15:
        return digits
    raise ValueError("That is not a usable phone number.")


def _under_daily_cap():
    cap = int(_setting("TERMII_DAILY_CAP", 500))
    key = f"termii:day:{timezone.now():%Y%m%d}"
    cache.add(key, 0, 90000)
    try:
        return cache.incr(key) <= cap
    except ValueError:
        return True


def _mask(number):
    return f"{number[:5]}****{number[-2:]}"


def send(phone, text):
    key, base = _setting("TERMII_API_KEY"), _setting("TERMII_BASE_URL").rstrip("/")
    if not key or not base:
        raise ImproperlyConfigured("TERMII_API_KEY and TERMII_BASE_URL must be set.")
    if base.endswith("/api"):
        base = base[:-4]
    number = to_msisdn(phone)
    if not _under_daily_cap():
        log.error("Termii daily cap reached; SMS to %s not sent", _mask(number))
        raise RuntimeError("Daily SMS cap reached.")

    payload = {
        "api_key": key, "to": number, "from": _setting("TERMII_SENDER_ID", "N-Alert"),
        "sms": text, "type": "plain", "channel": _setting("TERMII_CHANNEL", "dnd"),
    }
    request = Request(f"{base}/api/sms/send", data=json.dumps(payload).encode(), method="POST", headers={
        "Content-Type": "application/json", "User-Agent": "klosa-bank/1.0"})
    try:
        with urlopen(request, timeout=int(_setting("TERMII_TIMEOUT", 10))) as response:
            raw = response.read()
    except HTTPError as e:
        body = e.read()[:200].decode("utf-8", "replace")                  # Termii's reason, never our request
        log.error("Termii rejected SMS to %s (HTTP %s): %s", _mask(number), e.code, body)
        raise
    except URLError as e:
        log.error("Termii unreachable for SMS to %s: %s", _mask(number), e.reason)
        raise
    try:
        data = json.loads(raw)
    except ValueError:
        data = {}
    if str(data.get("code", "")).lower() != "ok":
        log.error("Termii did not confirm SMS to %s: %s", _mask(number), str(data)[:200])
        raise RuntimeError("The SMS was not confirmed.")
    return data.get("message_id", "")
