from decimal import Decimal

from django.db import models
from django.db.models import Q

from accounts.models import Account


class SavingsPlan(models.Model):
    KINDS = [("flex", "Flexible"), ("locked", "Locked")]
    STATUS = [("active", "Active"), ("closed", "Closed")]

    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="savings_plans")
    name = models.CharField(max_length=40)
    kind = models.CharField(max_length=10, choices=KINDS)
    reference = models.CharField(max_length=20, unique=True)      # makes creating a plan safe to retry
    target_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    principal = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))  # locked only
    rate = models.DecimalField(max_digits=5, decimal_places=2)    # percent a year, fixed when the plan opens
    interest_carry = models.DecimalField(max_digits=20, decimal_places=8, default=Decimal("0"))
    term_days = models.PositiveIntegerField(null=True, blank=True)
    start_date = models.DateField()
    maturity_date = models.DateField(null=True, blank=True)
    last_accrued_on = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS, default="active")
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["account", "status"]),
                   models.Index(fields=["status", "maturity_date"])]
        constraints = [
            models.CheckConstraint(condition=Q(balance__gte=0), name="savings_balance_nonneg"),
            models.CheckConstraint(condition=Q(principal__gte=0), name="savings_principal_nonneg"),
            models.CheckConstraint(condition=Q(rate__gte=0), name="savings_rate_nonneg"),
            models.CheckConstraint(condition=Q(kind="flex") | Q(maturity_date__isnull=False),
                                   name="savings_locked_has_maturity"),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_kind_display()})"


class SavingsEntry(models.Model):
    TYPES = [("deposit", "Deposit"), ("withdrawal", "Withdrawal"), ("interest", "Interest"),
             ("forfeit", "Interest given up"), ("maturity", "Paid out at maturity")]

    plan = models.ForeignKey(SavingsPlan, on_delete=models.PROTECT, related_name="entries")
    entry_type = models.CharField(max_length=12, choices=TYPES)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    balance_after = models.DecimalField(max_digits=15, decimal_places=2)
    reference = models.CharField(max_length=30, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="savings_entry_positive")]

    @property
    def is_credit(self):
        return self.entry_type in ("deposit", "interest")