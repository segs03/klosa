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
        ("savings_deposit", "Moved to savings"),
        ("savings_withdrawal", "Moved from savings"),
    ]
    
    CREDIT_TYPES = ("deposit", "savings_withdrawal")
    SAVINGS_TYPES = ("savings_deposit", "savings_withdrawal")
    EXPENSE_TYPES = ("withdrawal", "payment", "transfer")
    ICONS = {
        "deposit": "fa-arrow-down", "withdrawal": "fa-arrow-up", "transfer": "fa-paper-plane",
        "payment": "fa-receipt", "savings_deposit": "fa-piggy-bank",
        "savings_withdrawal": "fa-piggy-bank", "refund": "fa-rotate-left",
    }


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
    # biller recipient name
     
    counterparty_name = models.CharField(max_length=100, blank=True)
    counterparty_account = models.CharField(max_length=20, blank=True)

    created_at = models.DateTimeField(
        auto_now_add=True
    )
    
    @property
    def is_refund(self):
        return self.transaction_type == "deposit" and (self.description or "").startswith("Refund:")

    @property
    def kind(self):
        return "refund" if self.is_refund else self.transaction_type

    @property
    def label(self):
        return "Refund" if self.is_refund else self.get_transaction_type_display()

    @property
    def is_credit(self):
        return self.transaction_type in self.CREDIT_TYPES

    @property
    def is_savings(self):
        return self.transaction_type in self.SAVINGS_TYPES

    @property
    def category(self):
        if self.is_refund:
            return "other"                   # a refund is your own money coming back, not income
        if self.transaction_type == "deposit":
            return "income"
        if self.transaction_type in self.EXPENSE_TYPES:
            return "expense"
        return "other"

    @property
    def icon(self):
        return self.ICONS.get(self.kind, "fa-money-bill-wave")

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
       
       