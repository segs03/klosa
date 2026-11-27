
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
from .utils import normalize_email
from .storage import PrivateStorage

from django.db.models.functions import Lower





class User(AbstractUser):
    # Customers sign up with a phone number and may have no email. Staff must have one.
    email = models.EmailField(max_length=254, null=True, blank=True)
    email_normalized = models.CharField(unique=True, max_length=254, null=True, blank=True, editable=False)
    email_verified = models.BooleanField(default=False)

    # Phone is the customer's login, so it must be unique (added in stage 2).
    phone_number = models.CharField(unique=True, max_length=15, null=True, blank=True)
    phone_verified = models.BooleanField(default=False)

    must_change_password = models.BooleanField(default=False)
    
    email_verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:   # add to your existing Meta if you have one
        constraints = [models.UniqueConstraint(
        Lower("email"), condition=Q(email_verified_at__isnull=False), name="one_verified_email")]

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip() or None          # keep the address as typed
        self.email_normalized = normalize_email(self.email) if self.email else None
        self.phone_number = self.phone_number or None            # blank must be NULL for "unique"
        super().save(*args, **kwargs)

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
    
    kyc_tier = models.PositiveSmallIntegerField(default=0)
    transfers_locked_until = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    
    closed_at = models.DateTimeField(null=True, blank=True)
    
    face_verified_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_closed(self):
        return self.closed_at is not None

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

    @property
    def tier(self):
         return self.kyc_tier + 1

    def __str__(self):
        return f"{self.user.username} - {self.account_number}"
  

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
  
                            

class OneTimeCode(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    purpose = models.CharField(max_length=20)
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class BackupCode(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="backup_codes")
    code_hash = models.CharField(max_length=64, db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)


class StaffTOTP(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="totp")
    secret_enc = models.BinaryField()
    confirmed = models.BooleanField(default=False)
    last_step = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def create_for(cls, user):
        from . import totp
        return cls.objects.create(user=user, secret_enc=totp.encrypt(totp.new_secret()))

    def secret(self):
        from . import totp
        return totp.decrypt(bytes(self.secret_enc))



class FaceCheck(models.Model):
    STATUS = [("capturing", "Capturing"), ("review", "In review"), ("passed", "Passed"),
              ("failed", "Failed"), ("expired", "Expired")]
    LABELS = {"smile": "Smile", "blink": "Blink twice", "left": "Slowly turn your head left",
              "right": "Slowly turn your head right", "up": "Look up", "open": "Open your mouth"}

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="face_checks")
    status = models.CharField(max_length=10, choices=STATUS, default="capturing")
    prompts = models.JSONField(default=list)
    nonce_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    score = models.FloatField(null=True, blank=True)
    provider_ref = models.CharField(max_length=100, blank=True)
    reason = models.CharField(max_length=300, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [("review_facecheck", "Can review face checks")]

    def prompt_labels(self):
        return [self.LABELS.get(p, p) for p in self.prompts]


class FaceFrame(models.Model):
    face_check = models.ForeignKey(FaceCheck, on_delete=models.PROTECT, related_name="frames")
    step = models.PositiveSmallIntegerField()
    path = models.CharField(max_length=255, blank=True)        # emptied when the image is purged
    sha256 = models.CharField(max_length=64, unique=True)      # one photo can never be used twice
    taken_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["step"]
        constraints = [models.UniqueConstraint(fields=["face_check", "step"], name="one_frame_per_step")]



class RecoveryRequest(models.Model):
    STATUS = [("pending", "Pending"), ("approved", "Approved"),
              ("rejected", "Rejected"), ("done", "Done")]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True) 
               
                        
      
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
        
        
     
class CreditSettlement(models.Model):
    KINDS = [("asset_trade", "Asset sale"), ("external", "Payment received outside Klosa")]
    STATUS = [("pending", "Waiting for approval"), ("approved", "Approved"), ("rejected", "Rejected")]

    credit_account = models.ForeignKey("CreditAccount", on_delete=models.PROTECT, related_name="settlements")
    kind = models.CharField(max_length=12, choices=KINDS)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    asset = models.CharField(max_length=200)                   # what was sold, in plain words
    evidence_ref = models.CharField(max_length=100)            # a sale receipt, contract or bank reference
    reference = models.CharField(max_length=20, unique=True)
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    proposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [("propose_creditsettlement", "Can record a credit settlement"),
                       ("approve_creditsettlement", "Can approve a credit settlement")]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="settlement_amount_positive")]



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
    identity_document = models.FileField(upload_to="kyc/", storage=PrivateStorage(), blank=True)
    
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



class VerifiedDocument(models.Model):
    """One row per verified ID, so a person can hold a NIN and a BVN. Each ID belongs to one account."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="verified_documents")
    document_type = models.CharField(max_length=50)
    identity_digest = models.CharField(max_length=64, unique=True)
    verified_at = models.DateTimeField()
    source_application = models.OneToOneField(KYCApplication, on_delete=models.PROTECT, related_name="verified_document")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "document_type"], name="one_verified_document_per_type")]


class WelcomeBonus(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="welcome_bonus")
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    reference = models.CharField(max_length=20, unique=True)
    granted_at = models.DateTimeField(auto_now_add=True)