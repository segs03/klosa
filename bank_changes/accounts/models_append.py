# APPEND this class to accounts/models.py (the imports it needs are already there).
# Then: python manage.py makemigrations accounts && python manage.py migrate


class AddressProof(models.Model):
    STATUS_CHOICES = [("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")]
    DOC_TYPES = [("utility_bill", "Utility bill")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="address_proofs")
    line1 = models.CharField(max_length=150)
    line2 = models.CharField(max_length=150, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    country_code = models.CharField(max_length=2, default="NG")

    document_type = models.CharField(max_length=30, choices=DOC_TYPES, default="utility_bill")
    document = models.FileField(upload_to="address/", storage=PrivateStorage())   # private, never a public URL
    bill_date = models.DateField()

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    reason = models.CharField(max_length=500, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-submitted_at"]
        permissions = [("review_addressproof", "Can review address proofs")]
        constraints = [
            models.UniqueConstraint(fields=["user"], condition=Q(status="pending"),
                                    name="one_pending_address_per_user"),
            models.UniqueConstraint(fields=["user"], condition=Q(status="approved"),
                                    name="one_approved_address_per_user"),
        ]

    def __str__(self):
        return f"Address {self.pk} - {self.status}"
