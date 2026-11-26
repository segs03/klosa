"""Connects the face check to the rest of the app. Imported at the bottom of accounts/models.py."""
import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

log = logging.getLogger("kyc.audit")


@receiver(post_save, sender="face_auth.FaceCheck")
def face_check_saved(sender, instance, **kwargs):
    if instance.status == "passed":
        transaction.on_commit(lambda: after_pass(instance.pk))     # after the reviewer's or customer's save is final


def after_pass(check_pk):
    """Enrol the face template, look for the same face on other accounts, then try the welcome gift.
    Nothing here may break the pass itself, so every step logs and carries on."""
    from . import face_templates as ft
    from accounts.bonus import grant_welcome_bonus
    from .models import FaceCheck, FaceTemplate

    check = FaceCheck.objects.select_related("user").filter(pk=check_pk, status="passed").first()
    if check is None:
        return
    try:
        if ft.enabled() and not FaceTemplate.objects.filter(user=check.user, check=check).exists():
            vectors = ft.embeddings_for_check(check)
            if vectors:
                vector = ft.enroll(check.user, vectors, check)
                matches = ft.scan(check.user, vector)
                ft.record_duplicates(check.user, matches)
                if matches:
                    log.warning("Face check %s: user %s looks like %d other account(s)", check.pk, check.user_id, len(matches))
            else:
                log.warning("Face check %s passed but had no usable photos for a template", check.pk)
    except Exception:
        log.exception("Face template step failed for check %s", check_pk)
    try:
        grant_welcome_bonus(check.user)
    except Exception:
        log.exception("Welcome gift step failed after face check %s", check_pk)


@receiver(post_save, sender="accounts.Account")
def account_saved(sender, instance, **kwargs):
    if instance.closed_at:
        from .face_templates import delete_for_user
        delete_for_user(instance.user_id)
