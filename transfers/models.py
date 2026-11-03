from django.db import models
from accounts.models import Account

from django.conf import settings
from django.core.validators import MinValueValidator
from decimal import Decimal



class Transfer(models.Model):

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    sender = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="sent_transfers"
    )

    receiver = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="received_transfers"
    )

    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2
    )

    reference = models.CharField(
        max_length=100,
        unique=True
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending"
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return self.reference
        
        
        
        
        
class MoneyRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('declined', 'Declined'),
        ('cancelled', 'Cancelled'),
        ('expired', 'Expired'),
    ]

    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='money_requests_sent'
    )

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='money_requests_received'
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )

    note = models.CharField(max_length=100, blank=True)

    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='pending'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)