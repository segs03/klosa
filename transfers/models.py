from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from accounts.models import Account

POSITIVE = [MinValueValidator(Decimal("0.01"))]


class Transfer(models.Model):
    STATUS_CHOICES = [("pending", "Pending"), ("completed", "Completed"), ("failed", "Failed")]

    sender = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="sent_transfers")
    receiver = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="received_transfers")
    amount = models.DecimalField(max_digits=15, decimal_places=2, validators=POSITIVE)
    reference = models.CharField(max_length=100, unique=True)
    note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["sender", "status", "created_at"])]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="transfer_amount_positive"),
            models.CheckConstraint(condition=~Q(sender=F("receiver")), name="transfer_not_to_self"),
        ]

    def __str__(self):
        return self.reference


class MoneyRequest(models.Model):
    STATUS_CHOICES = [("pending", "Pending"), ("paid", "Paid"), ("declined", "Declined"),
                      ("cancelled", "Cancelled"), ("expired", "Expired")]

    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name="money_requests_sent")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name="money_requests_received")
    amount = models.DecimalField(max_digits=15, decimal_places=2, validators=POSITIVE)
    note = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")

    # a request can be paid once: the link below is one-to-one
    transfer = models.OneToOneField(Transfer, null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="money_request")
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "status"])]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="request_amount_positive"),
            models.CheckConstraint(condition=~Q(requester=F("recipient")), name="request_not_to_self"),
            models.CheckConstraint(
                condition=~Q(status="paid") | Q(transfer__isnull=False),
                name="request_paid_needs_transfer"),
        ]

    @property
    def is_expired(self):
        return bool(self.expires_at and self.expires_at <= timezone.now())