from django.db import models
from accounts.models import Account, CreditAccount

from django.conf import settings
from django.core.validators import MinValueValidator
from decimal import Decimal



class Transaction(models.Model):

    TRANSACTION_TYPES = [
        ("deposit", "Deposit"),
        ("withdrawal", "Withdrawal"),
        ("transfer", "Transfer"),
        ("payment", "Payment"),
    ]

    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="transactions"
    )

    transaction_type = models.CharField(
        max_length=20,
        choices=TRANSACTION_TYPES
    )
    
    # transaction/models.py  (inside Transaction)
    beneficiary = models.ForeignKey("beneficiary.Beneficiary", on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="transactions")
    reference = models.CharField(max_length=20, blank=True, db_index=True)

    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2
    )

    description = models.CharField(
        max_length=255,
        blank=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return f"{self.transaction_type} - {self.amount}"
        
        
        
class CreditTransaction(models.Model):
    TYPE_CHOICES = [
        ("purchase", "Purchase"),
        ("repayment", "Repayment"),
        ("refund", "Refund"),
        ("reversal", "Reversal"),
    ]

    credit_account = models.ForeignKey(
        CreditAccount,
        on_delete=models.PROTECT,
        related_name="transactions"
    )

    transaction_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES
    )

    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2
    )

    reference = models.CharField(
        max_length=100,
        unique=True
    )

    description = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.transaction_type}: {self.amount}"
       
       