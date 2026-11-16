from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    action = models.CharField(max_length=40)
    target = models.CharField(max_length=60, blank=True)
    detail = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("view_customers", "Can look up customers"),
            ("manage_customers", "Can unlock and suspend customers"),
            ("view_operations", "Can see stuck payments"),
            ("view_audit", "Can read the audit log"),
        ]