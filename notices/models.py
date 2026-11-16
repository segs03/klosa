from django.conf import settings
from django.db import models
from django.db.models import Q


class Notification(models.Model):
    LEVELS = [("info", "Info"), ("success", "Success"), ("warning", "Warning"), ("alert", "Alert")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=80)
    body = models.CharField(max_length=240, blank=True)
    level = models.CharField(max_length=8, choices=LEVELS, default="info")
    link = models.CharField(max_length=200, blank=True)
    key = models.CharField(max_length=60, blank=True)          # a non-empty key means "send this only once"
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["user", "read_at", "created_at"])]
        constraints = [models.UniqueConstraint(fields=["user", "key"], condition=~Q(key=""),
                                               name="one_notice_per_key")]