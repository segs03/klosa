"""Email verification for tier upgrades.

Codes live in the cache (hashed, bound to the exact address, 10 minutes), never in the database.
Import this module from AccountsConfig.ready() so the pre_save receiver below is registered.
"""
import hashlib
import hmac
import logging
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils import timezone

from .utils import hit

log = logging.getLogger("kyc.audit")
TTL, MAX_TRIES = 600, 5


def normalize(email):
    return (email or "").strip().lower()


def _k(kind, user):
    return f"emailv:{kind}:{user.pk}"


def _digest(email, code):
    return hmac.new(settings.SECRET_KEY.encode(), f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


def _taken(user, email):
    """Another account already owns this address (verified)."""
    return (get_user_model().objects.filter(email__iexact=email, email_verified_at__isnull=False)
            .exclude(pk=user.pk).exists())


def send_code(user, email):
    email = normalize(email)
    if _taken(user, email):
        raise ValidationError("This email can't be used.")
    if hit(f"emailv_cd:{user.pk}", 1, 60) or hit(f"emailv:{user.pk}", 5, 3600):     # 1 a minute, 5 an hour
        raise ValidationError("Too many requests. Wait a minute and try again.")
    code = f"{secrets.randbelow(10 ** 6):06d}"
    cache.set(_k("code", user), {"email": email, "h": _digest(email, code)}, TTL)   # a new send replaces the old code
    cache.delete(_k("tries", user))
    try:
        send_mail(
            "Your Klosa verification code",
            f"Your Klosa Bank code is {code}. It expires in 10 minutes.\n\n"
            "If you did not ask for it, ignore this email. Never share this code with anyone.",
            settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
    except Exception:
        log.exception("verification email failed for user %s", user.pk)       # never log the code
        cache.delete(_k("code", user))
        raise ValidationError("We could not send the email. Try again later.")


def check_code(user, code):
    data = cache.get(_k("code", user))
    if not data:
        return False
    cache.add(_k("tries", user), 0, TTL)
    try:
        tries = cache.incr(_k("tries", user))
    except ValueError:
        tries = 1
    if tries > MAX_TRIES:
        cache.delete(_k("code", user))                                        # too many guesses: burn it
        return False
    if not hmac.compare_digest(data["h"], _digest(data["email"], (code or "").strip())):
        return False
    return _commit(user, data["email"])


def _commit(user, email):
    User = get_user_model()
    try:
        with transaction.atomic():
            if _taken(user, email):                                           # someone verified it first
                return False
            # .update() on purpose: it skips the reset receiver below
            User.objects.filter(pk=user.pk).update(email=email, email_verified_at=timezone.now())
    except IntegrityError:                                                    # the unique constraint caught a race
        return False
    cache.delete(_k("code", user))
    cache.delete(_k("tries", user))
    user.refresh_from_db(fields=["email", "email_verified_at"])
    from .tiers import refresh_tier
    refresh_tier(user)
    return True


@receiver(pre_save, sender=settings.AUTH_USER_MODEL)
def reset_on_email_change(sender, instance, **kwargs):
    """Changing the address (profile form, admin, .save()) drops the verification.
    Not covered: save(update_fields=["email"]) without email_verified_at."""
    if not instance.pk or not instance.email_verified_at:
        return
    old = sender.objects.filter(pk=instance.pk).values_list("email", flat=True).first()
    if old is not None and old.lower() != (instance.email or "").lower():
        instance.email_verified_at = None
