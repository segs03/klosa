import logging

from django.db import IntegrityError, transaction

from .models import Notification

log = logging.getLogger(__name__)


def notify_user(user, title, body="", level="info", link="", key=""):
    """Never raises. It runs inside the action's own transaction, so a notice for something that
    rolled back disappears with it. Never put a token, PIN or full number in a notice."""
    if user is None or not getattr(user, "pk", None):
        return None
    if level not in ("info", "success", "warning", "alert"):
        level = "info"
    link = link if (link.startswith("/") and not link.startswith("//")) else ""    # inside links only
    try:
        with transaction.atomic():                          # a savepoint, so a failure cannot break the caller
            return Notification.objects.create(user=user, title=title[:80], body=(body or "")[:240],
                                               level=level, link=link[:200], key=(key or "")[:60])
    except IntegrityError:
        return None                                         # this one-time notice was already sent
    except Exception:
        log.exception("Could not save a notification for user %s", user.pk)
        return None