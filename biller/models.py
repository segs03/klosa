from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from accounts.models import Account


class BillPayment(models.Model):
    CATEGORIES = [
        ("airtime", "Airtime"), ("data", "Data"), ("education", "Education"),
        ("electricity", "Electricity"), ("tv", "TV"), ("internet", "Internet"),
        ("betting", "Betting"), ("other", "Other bills"),
    ]
    STATUS = [
        ("processing", "Processing"), ("completed", "Completed"),
        ("reversed", "Refunded"),
    ]

    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="bill_payments")
    category = models.CharField(max_length=20, choices=CATEGORIES)
    provider_code = models.CharField(max_length=30)          # for example "mtn"
    product_code = models.CharField(max_length=60, blank=True)   # data plan, bill type
    customer_ref = models.CharField(max_length=30)           # phone, meter, smartcard, betting ID
    amount = models.DecimalField(max_digits=12, decimal_places=2,
                                 validators=[MinValueValidator(Decimal("0.01"))])
    fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    reference = models.CharField(max_length=20, unique=True)
    provider_reference = models.CharField(max_length=100, blank=True)
    token = models.CharField(max_length=255, blank=True)     # electricity token, exam PIN: SENSITIVE
    status = models.CharField(max_length=12, choices=STATUS, default="processing")
    failure_reason = models.CharField(max_length=200, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)   # set when one worker claims the order
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["account", "status", "created_at"]),
            models.Index(fields=["status", "submitted_at"]),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="bill_amount_positive"),
            models.CheckConstraint(condition=Q(fee__gte=0), name="bill_fee_nonnegative"),
        ]

    def __str__(self):
        return f"{self.get_category_display()} {self.reference}"