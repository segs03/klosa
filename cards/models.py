
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from accounts.models import Account


class BankCard(models.Model):
    CARD_TYPES = [
        ("debit", "Debit Card"),
        ("credit", "Credit Card"),
    ]

    CARD_STATUSES = [
        ("active", "Active"),
        ("blocked", "Blocked"),
        ("expired", "Expired"),
        ("cancelled", "Cancelled"),
    ]

    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="cards",
    )

    card_type = models.CharField(
        max_length=10,
        choices=CARD_TYPES,
        default="debit",
    )

    cardholder_name = models.CharField(max_length=100)
    last_four = models.CharField(max_length=4)

    expiry_month = models.PositiveSmallIntegerField()
    expiry_year = models.PositiveSmallIntegerField()

    status = models.CharField(
        max_length=10,
        choices=CARD_STATUSES,
        default="active",
    )

    # References supplied by the card issuer.
    # Never store the full card number or CVV here.
    provider_name = models.CharField(max_length=50, blank=True)
    provider_card_id = models.CharField(
        max_length=150,
        null=True,
        blank=True,
        unique=True,
    )
    provider_token = models.CharField(max_length=255, blank=True)

    # Whether online purchases are permitted.
    online_enabled = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(expiry_month__gte=1) &
                          Q(expiry_month__lte=12),
                name="card_expiry_month_valid",
            ),
            models.CheckConstraint(
                condition=Q(last_four__regex=r"^[0-9]{4}$"),
                name="card_last_four_numeric",
            ),
        ]

    def __str__(self):
        return (
            f"{self.get_card_type_display()} "
            f"•••• {self.last_four}"
        )

    @property
    def expiry_display(self):
        if self.expiry_month and self.expiry_year:
            return (
                f"{self.expiry_month:02d}/"
                f"{self.expiry_year % 100:02d}"
            )
        return "--/--"

    @property
    def masked_number(self):
        return f"•••• •••• •••• {self.last_four}"


class CardOrder(models.Model):
    CARD_TYPES = [
        ("debit", "Debit Card"),
        ("credit", "Credit Card"),
    ]

    CARD_FORMATS = [
        ("physical", "Physical"),
        ("virtual", "Virtual"),
    ]

    STATUSES = [
    ("pending", "Pending Review"),
    ("approved", "Approved"),
    ("issuing", "Issuing"),
    ("issued", "Issued"),
    ("rejected", "Rejected"),
    ("failed", "Issuance Failed"),
    ("cancelled", "Cancelled"),
]

    reference = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )

    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="card_orders",
    )

    card_type = models.CharField(
        max_length=10,
        choices=CARD_TYPES,
        default="debit",
    )

    card_format = models.CharField(
        max_length=10,
        choices=CARD_FORMATS,
    )

    cardholder_name = models.CharField(max_length=100)

    # Only required for physical orders.
    delivery_address = models.TextField(blank=True)
    delivery_city = models.CharField(max_length=100, blank=True)
    delivery_state = models.CharField(max_length=100, blank=True)
    delivery_phone = models.CharField(max_length=20, blank=True)

    status = models.CharField(
        max_length=12,
        choices=STATUSES,
        default="pending",
    )

    bank_card = models.OneToOneField(
        BankCard,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="order",
    )

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reviewed_card_orders",
    )

    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        
        permissions = [
        ("approve_cardorder", "Can approve physical card orders"),
    ]

    def __str__(self):
        return f"Card order {self.reference}"


class CardDelivery(models.Model):
    DELIVERY_STATUSES = [
        ("not_dispatched", "Not Dispatched"),
        ("dispatched", "Dispatched"),
        ("in_transit", "In Transit"),
        ("delivered", "Delivered"),
        ("delivery_failed", "Delivery Failed"),
    ]

    order = models.OneToOneField(
        CardOrder,
        on_delete=models.PROTECT,
        related_name="delivery",
    )

    tracking_number = models.CharField(max_length=100, blank=True)
    courier = models.CharField(max_length=100, blank=True)

    status = models.CharField(
        max_length=20,
        choices=DELIVERY_STATUSES,
        default="not_dispatched",
    )

    dispatched_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Delivery for {self.order.reference}"


class CardTransaction(models.Model):
    TRANSACTION_TYPES = [
        ("purchase", "Online Purchase"),
        ("refund", "Refund"),
        ("reversal", "Reversal"),
    ]

    STATUSES = [
        ("pending", "Pending"),
        ("authorized", "Authorized"),
        ("declined", "Declined"),
        ("settled", "Settled"),
        ("reversed", "Reversed"),
    ]

    card = models.ForeignKey(
        BankCard,
        on_delete=models.PROTECT,
        related_name="transactions",
    )

    transaction_type = models.CharField(
        max_length=10,
        choices=TRANSACTION_TYPES,
    )

    status = models.CharField(
        max_length=12,
        choices=STATUSES,
        default="pending",
    )

    merchant_name = models.CharField(max_length=150)
    merchant_reference = models.CharField(max_length=150, blank=True)

    amount = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=3, default="NGN")

    # Unique identifier from the issuer.
    provider_transaction_id = models.CharField(
        max_length=150,
        unique=True,
    )

    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["card", "occurred_at"]),
            models.Index(fields=["status", "occurred_at"]),
        ]

    def __str__(self):
        return f"{self.merchant_name} - {self.amount} {self.currency}"


class CardProviderEvent(models.Model):
    # Prevents the same webhook event from being processed twice.
    provider_name = models.CharField(max_length=50)
    event_id = models.CharField(max_length=150)

    event_type = models.CharField(max_length=100)
    processed = models.BooleanField(default=False)

    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider_name", "event_id"],
                name="unique_card_provider_event",
            ),
        ]

    def __str__(self):
        return f"{self.provider_name}: {self.event_type}"
