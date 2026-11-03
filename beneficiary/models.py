
from django.conf import settings
from django.db import models


class Beneficiary(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="beneficiaries",
        null=True,
        blank=True,
    )

    name = models.CharField(max_length=100)
    account_number = models.CharField(max_length=10)
    bank_name = models.CharField(max_length=100)
    bank_code = models.CharField(max_length=10, blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "bank_code", "account_number"],
                name="unique_beneficiary_per_owner",
            )
        ]

    def __str__(self):
        return f"{self.name} - {self.bank_name}"
