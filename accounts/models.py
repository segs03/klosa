
from django.db import models
from django.contrib.auth.models import User
from datetime import timedelta
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from django.core.validators import MinValueValidator, MaxValueValidator
from decimal import Decimal




class Account(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE
    )

    account_number = models.CharField(
        max_length=10,
        unique=True
    )

    balance = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0
    )

    currency = models.CharField(
        max_length=3,
        default="NGN"
    )

    pin_hash = models.CharField(max_length=128, blank=True)
    pin_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def set_pin(self, raw):
        self.pin_hash = make_password(raw)
        self.pin_attempts = 0
        self.locked_until = None
        self.save(update_fields=[
            "pin_hash", "pin_attempts", "locked_until"
        ])

    def is_locked(self):
        return bool(
            self.locked_until and
            self.locked_until > timezone.now()
        )

    def check_pin(self, raw):
        if self.is_locked() or not self.pin_hash:
            return False

        if check_password(raw, self.pin_hash):
            if self.pin_attempts:
                self.pin_attempts = 0
                self.save(update_fields=["pin_attempts"])
            return True

        self.pin_attempts += 1

        if self.pin_attempts >= 3:
            self.locked_until = (
                timezone.now() + timedelta(minutes=15)
            )
            self.pin_attempts = 0

        self.save(update_fields=[
            "pin_attempts", "locked_until"
        ])

        return False

    def __str__(self):
        return f"{self.user.username} - {self.account_number}"
        
        
      
class CreditAccount(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("active", "Active"),
        ("suspended", "Suspended"),
        ("closed", "Closed"),
    ]

    account = models.OneToOneField(
        Account,
        on_delete=models.CASCADE,
        related_name="credit_facility"
    )

    credit_limit = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal("0.00")
    )

    outstanding_balance = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal("0.00")
    )

    credit_score = models.PositiveSmallIntegerField(
        default=650,
        validators=[
            MinValueValidator(300),
            MaxValueValidator(850)
        ]
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending"
    )

    auto_repayment = models.BooleanField(default=False)

    repayment_due_date = models.DateField(
        null=True,
        blank=True
    )

    approved_at = models.DateTimeField(
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def available_credit(self):
        return max(
            self.credit_limit - self.outstanding_balance,
            Decimal("0.00")
        )

    def __str__(self):
        return f"{self.account.user.username} Credit Facility"


