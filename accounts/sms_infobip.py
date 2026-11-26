"""Infobip SMS sender. Plugs into accounts.services.deliver_sms via SMS_BACKEND=accounts.sms_infobip.send"""
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
OK_GROUPS = {1, 3}      # Infobip status groups: 1 PENDING, 3 DELIVERED. 2, 4, 5 are failures.


def to_msisdn(phone):
    d = re.sub(r"\D", "", str(phone or ""))
    if d.startswith("234") and len(d) == 13:
        return d
    if d.startswith("0") and len(d) == 11:
        return "234" + d[1:]
    if len(d) == 10:
        return "234" + d
    raise ValueError("Unusable phone number.")


def _config():
    key = getattr(settings, "INFOBIP_API_KEY", "")
    base = (getattr(settings, "INFOBIP_BASE_URL", "") or "").rstrip("/")
    if not key:
        raise ImproperlyConfigured("INFOBIP_API_KEY is not set.")
    if not base:
        raise ImproperlyConfigured("INFOBIP_BASE_URL is not set.")
    return key, base


def _under_daily_cap():
    cap = int(getattr(settings, "INFOBIP_DAILY_CAP", 500))
    k = f"sms:infobip:{timezone.localdate()}"
    cache.add(k, 0, 90000)
    return cache.incr(k) <= cap


def send(phone, text):
    key, base = _config()
    to = to_msisdn(phone)
    if not _under_daily_cap():
        log.error("Infobip daily SMS cap reached; message not sent.")
        raise RuntimeError("Daily SMS cap reached.")

    message = {"destinations": [{"to": to}], "text": text}
    sender = getattr(settings, "INFOBIP_SENDER", "")
    if sender:
        message["from"] = sender
    request = Request(f"{base}/sms/2/text/advanced",
                      data=json.dumps({"messages": [message]}).encode(), method="POST",
                      headers={"Authorization": f"App {key}", "Content-Type": "application/json",
                               "Accept": "application/json", "User-Agent": "klosa-bank/1.0"})
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read()
    except HTTPError as e:
        log.error("Infobip rejected an SMS (HTTP %s): %s", e.code, e.read()[:300].decode("utf-8", "replace"))
        raise
    except URLError as e:
        log.error("Infobip could not be reached: %s", e.reason)
        raise

    try:
        item = json.loads(raw)["messages"][0]
        group = item["status"]["groupId"]
    except (ValueError, KeyError, IndexError, TypeError):
        log.error("Infobip sent an unreadable reply.")
        raise RuntimeError("Unreadable Infobip reply.")
    if group not in OK_GROUPS:
        log.error("Infobip did not accept the SMS: %s", item["status"].get("name", group))
        raise RuntimeError("Infobip did not accept the SMS.")
    return item.get("messageId")
