from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q

from accounts.models import Account

POSITIVE = [MinValueValidator(Decimal("0.01"))]


class CashRequest(models.Model):
    """Cash handed to a teller. It is credited only after a different staff member approves."""
    STATUS = [("pending", "Pending"), ("posted", "Posted"), ("rejected", "Rejected")]

    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="cash_requests")
    amount = models.DecimalField(max_digits=15, decimal_places=2, validators=POSITIVE)
    branch = models.CharField(max_length=60)
    note = models.CharField(max_length=200, blank=True)
    reference = models.CharField(max_length=20, unique=True)
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    maker = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    checker = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("record_cash", "Can record cash deposits"),
            ("approve_cash", "Can approve cash deposits"),
            ("pay_cashout", "Can pay out cash withdrawals"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="cashreq_amount_positive"),
            models.CheckConstraint(condition=~Q(status="posted") | Q(checker__isnull=False),
                                   name="cashreq_posted_has_checker"),
            models.CheckConstraint(condition=~Q(maker=F("checker")), name="cashreq_two_people"),
        ]


class CashOut(models.Model):
    """Money already taken from the customer, waiting to be collected as cash."""
    STATUS = [("held", "Waiting for collection"), ("paid", "Paid out"), ("expired", "Expired and refunded")]

    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="cash_outs")
    amount = models.DecimalField(max_digits=15, decimal_places=2, validators=POSITIVE)
    reference = models.CharField(max_length=20, unique=True)
    code_hash = models.CharField(max_length=64)               # the 6-digit code is never stored
    status = models.CharField(max_length=10, choices=STATUS, default="held")
    expires_at = models.DateTimeField()
    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.PROTECT, related_name="+")
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "expires_at"])]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="cashout_amount_positive")]