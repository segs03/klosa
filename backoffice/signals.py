from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .events import record


@receiver(user_logged_in)
def _in(sender, request, user, **kw):
    record(request, "login", user=user)


@receiver(user_logged_out)
def _out(sender, request, user, **kw):
    record(request, "logout", user=user)


@receiver(user_login_failed)
def _failed(sender, credentials, request=None, **kw):
    record(request, "login_failed", username=str(credentials.get("username", "")))