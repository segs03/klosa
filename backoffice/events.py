import logging

from django.db import transaction

from .models import SecurityEvent


def record(request, event, user=None, username="", detail=""):
    try:
        meta = request.META if request is not None else {}
        with transaction.atomic():
            SecurityEvent.objects.create(
                user=user if getattr(user, "pk", None) else None,
                username=(username or getattr(user, "username", "") or "")[:150], event=event[:40],
                ip=meta.get("REMOTE_ADDR") or None, agent=meta.get("HTTP_USER_AGENT", "")[:200], detail=detail[:200])
    except Exception:
        logging.getLogger("security").exception("could not record security event %s", event)