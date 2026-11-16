from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from accounts.models import Account

POSITIVE = [MinValueValidator(Decimal("0.01"))]


class Deposit(models.Model):
    STATUS = [("pending", "Pending"), ("completed", "Completed"), ("failed", "Failed"),
              ("expired", "Expired"), ("review", "Needs review")]
    METHODS = [("card", "Card"), ("transfer", "Bank transfer")]

    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="deposits")
    method = models.CharField(max_length=10, choices=METHODS, default="card")
    amount = models.DecimalField(max_digits=15, decimal_places=2, validators=POSITIVE)
    paid_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    reference = models.CharField(max_length=20, unique=True)
    provider_reference = models.CharField(max_length=100, blank=True)
    checkout_url = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"])]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="deposit_amount_positive"),
            models.UniqueConstraint(fields=["provider_reference"], condition=~Q(provider_reference=""),
                                    name="deposit_provider_ref_unique"),
        ]


class Withdrawal(models.Model):
    STATUS = [("processing", "Processing"), ("completed", "Completed"), ("reversed", "Refunded")]

    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="withdrawals")
    bank_name = models.CharField(max_length=100)
    bank_code = models.CharField(max_length=10)
    account_number = models.CharField(max_length=10)
    account_name = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=15, decimal_places=2, validators=POSITIVE)
    fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    reference = models.CharField(max_length=20, unique=True)
    provider_reference = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default="processing")
    failure_reason = models.CharField(max_length=200, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)     # set when one worker claims the order
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["account", "status", "created_at"]),
                   models.Index(fields=["status", "submitted_at"])]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="withdrawal_amount_positive"),
            models.CheckConstraint(condition=Q(fee__gte=0), name="withdrawal_fee_nonnegative"),
        ]