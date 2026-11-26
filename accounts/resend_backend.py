"""Django email backend for Resend (https://resend.com), over HTTPS.

Why not SMTP: Render blocks outbound SMTP on free instances, while HTTPS always works.
Every send_mail() / EmailMessage in the project goes through this once EMAIL_BACKEND points at it.
No extra package needed.
"""
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend

log = logging.getLogger("email")
API_URL = "https://api.resend.com/emails"


class ResendBackend(BaseEmailBackend):
    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.key = getattr(settings, "RESEND_API_KEY", "")
        if not self.key:
            raise ImproperlyConfigured("RESEND_API_KEY is not set.")
        self.timeout = int(getattr(settings, "EMAIL_TIMEOUT", 10) or 10)

    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            try:
                self._post(self._payload(message))
                sent += 1
            except Exception:
                if not self.fail_silently:
                    raise
        return sent

    @staticmethod
    def _payload(m):
        if m.attachments:
            raise ValueError("Attachments are not supported by the Resend backend.")   # never drop one silently
        if not (m.to or m.bcc):
            raise ValueError("The email has no recipients.")
        payload = {"from": m.from_email, "to": list(m.to or m.bcc), "subject": m.subject}
        if m.to and m.bcc:
            payload["bcc"] = list(m.bcc)
        if m.cc:
            payload["cc"] = list(m.cc)
        if m.reply_to:
            payload["reply_to"] = list(m.reply_to)
        html = next((body for body, kind in getattr(m, "alternatives", []) if kind == "text/html"), None)
        if m.content_subtype == "html":
            payload["html"] = m.body
        else:
            payload["text"] = m.body
            if html:
                payload["html"] = html
        return payload

    def _post(self, payload):
        request = Request(API_URL, data=json.dumps(payload).encode(), method="POST", headers={
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "User-Agent": "klosa-bank/1.0",          # Resend's edge rejects requests with no user agent
        })
        try:
            with urlopen(request, timeout=self.timeout) as response:
                response.read()
        except HTTPError as e:
            body = e.read()[:300].decode("utf-8", "replace")
            log.error("Resend rejected an email (HTTP %s): %s", e.code, body)     # e.g. domain not verified
            raise
        except URLError as e:
            log.error("Resend could not be reached: %s", e.reason)
            raise
