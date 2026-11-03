from django.db import models
from accounts.models import Account


class BankCard(models.Model):
    CARD_TYPES = [("debit", "Debit Card"), ("credit", "Credit Card")]
    CARD_STATUSES = [("active", "Active"), ("blocked", "Blocked"), ("expired", "Expired")]

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="cards")
    card_type = models.CharField(max_length=10, choices=CARD_TYPES, default="debit")
    cardholder_name = models.CharField(max_length=100)
    last_four = models.CharField(max_length=4)
    expiry_month = models.PositiveSmallIntegerField()
    expiry_year = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=10, choices=CARD_STATUSES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_card_type_display()} •••• {self.last_four}"
        
    def save(self, *args, **kwargs):
        if not self.expiry_month or not self.expiry_year:
            today = date.today()
            self.expiry_month = today.month
            self.expiry_year = today.year + 5
        super().save(*args, **kwargs)

    @property
    def expiry_display(self):
        if self.expiry_month and self.expiry_year:
            return f"{self.expiry_month:02d}/{self.expiry_year % 100:02d}"
        return "--/--"

    @property
    def masked_number(self):
        return f"•••• •••• •••• {self.last_four}"


