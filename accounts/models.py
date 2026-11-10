
from django.db import models
from datetime import timedelta
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from django.core.validators import MinValueValidator, MaxValueValidator
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q

from django.contrib.auth.models import AbstractUser




class User(AbstractUser):
    email = models.EmailField(
        unique=True,
        null=False,
        blank=False
    )

    # Phone number is a contact detail, not an identity key.
    phone_number = models.CharField(
        max_length=20,
        blank=True
    )

    phone_verified = models.BooleanField(default=False)

    def __str__(self):
        return self.username




class Account(models.Model):
    
    user = models.OneToOneField(
    settings.AUTH_USER_MODEL,
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




class CreditPolicy(models.Model):
    ACCOUNT_LEVELS = [
        ("basic", "Basic"),
        ("verified", "KYC Verified"),
    ]

    account_level = models.CharField(
        max_length=20,
        choices=ACCOUNT_LEVELS,
    )

    currency = models.CharField(
        max_length=3,
        default="NGN",
    )

    minimum_limit = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    maximum_limit = models.DecimalField(
        max_digits=15,
        decimal_places=2,
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["account_level", "currency"],
                name="unique_credit_policy_level_currency",
            ),
        ]

    def clean(self):
        super().clean()

        if self.minimum_limit < 0:
            raise ValidationError(
                "Minimum credit limit cannot be negative."
            )

        if self.maximum_limit <= 0:
            raise ValidationError(
                "Maximum credit limit must be greater than zero."
            )

        if self.minimum_limit > self.maximum_limit:
            raise ValidationError(
                "Minimum credit limit cannot exceed maximum limit."
            )

    def __str__(self):
        return f"{self.get_account_level_display()} - {self.currency}"



class CreditApplication(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("assessed", "Assessed"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="credit_applications",
    )

    requested_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
    )

    assessed_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        null=True,
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )

    assessment_reason = models.CharField(
        max_length=500,
        blank=True,
    )

    decision_reason = models.CharField(
        max_length=500,
        blank=True,
    )

    assessed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="credit_assessments",
    )

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="credit_approvals",
    )

    submitted_at = models.DateTimeField(auto_now_add=True)
    assessed_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-submitted_at"]
        permissions = [
            ("assess_creditapplication", "Can assess credit applications"),
            ("approve_creditapplication", "Can approve credit applications"),
        ]

    def __str__(self):
        return f"Credit application {self.pk} - {self.status}"



class KYCApplication(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("under_review", "Under Review"),
        ("additional_info", "Additional Information Required"),
        ("verified", "Verified"),
        ("rejected", "Rejected"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="kyc_applications",
    )

    country_code = models.CharField(max_length=2)
    document_type = models.CharField(max_length=50)

    legal_name = models.CharField(max_length=150)
    date_of_birth = models.DateField()

    # Store documents using private storage, not public media URLs.
    identity_document = models.FileField(
        upload_to="private/kyc/",
    )
    
    identity_digest = models.CharField(
    max_length=64,
    blank=True,
    default="",
)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )

    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
          
        permissions = [
        (
            "review_kycapplication",
            "Can review KYC applications",
        ),
    ]
        ordering = ["-submitted_at"]


    def __str__(self):
        return f"KYC {self.user_id} - {self.status}"


class KYCReview(models.Model):
    DECISION_CHOICES = [
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("additional_info", "Additional Information Required"),
    ]

    application = models.ForeignKey(
        KYCApplication,
        on_delete=models.PROTECT,
        related_name="reviews",
    )

    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="kyc_reviews",
    )

    decision = models.CharField(
        max_length=20,
        choices=DECISION_CHOICES,
    )

    reason = models.CharField(max_length=500)

    reviewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["reviewed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["application", "reviewer"],
                name="unique_kyc_review_per_staff",
            ),
        ]

    def clean(self):
        super().clean()

        if self.application_id and self.reviewer_id:
            if self.application.user_id == self.reviewer_id:
                raise ValidationError(
                    "Users cannot review their own KYC applications."
                )

    def __str__(self):
        return f"KYC review {self.application_id} - {self.decision}"


class VerifiedIdentity(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="verified_identity",
    )

    country_code = models.CharField(max_length=2)
    document_type = models.CharField(max_length=50)

    # HMAC digest of the normalized identity reference.
    # Never store the raw identity number here.
    identity_digest = models.CharField(
        max_length=64,
        unique=True,
    )

    verified_at = models.DateTimeField()

    source_application = models.OneToOneField(
        KYCApplication,
        on_delete=models.PROTECT,
        related_name="verified_identity",
    )

    def __str__(self):
        return f"Verified identity - User {self.user_id}"




class Passkey(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="passkeys")
    credential_id = models.CharField(max_length=512, unique=True)
    public_key = models.BinaryField()
    sign_count = models.PositiveIntegerField(default=0)
    name = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username}: {self.name or 'passkey'}"
