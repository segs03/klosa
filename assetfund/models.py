import re
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models
from django.db.models import F, Q

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
LIVE = ("pending", "assessed", "approved", "active", "overdue")      # a pledge in any of these blocks the same asset


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def norm_ref(text):
    """'assay 1001-a' and 'ASSAY1001A' are the same asset."""
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


class AssetApplication(models.Model):
    KINDS = [("gold", "Gold or precious metal"), ("jewelry", "Jewellery"), ("property", "Land or property"),
             ("equipment", "Equipment or machinery"), ("materials", "Materials or stock"),
             ("skill_contract", "A skill, backed by a signed order"), ("other", "Other")]
    STATUS = [("pending", "Waiting for review"), ("assessed", "Valued, waiting for approval"),
              ("approved", "Offer made"), ("active", "Funding in progress"), ("overdue", "Past its term"),
              ("settled", "Fully recovered"), ("rejected", "Rejected"), ("withdrawn", "Withdrawn"),
              ("expired", "Offer expired")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="asset_applications")
    kind = models.CharField(max_length=16, choices=KINDS)
    title = models.CharField(max_length=120)
    description = models.CharField(max_length=1000)
    asset_ref = models.CharField(max_length=80)                    # serial, title deed, assay or contract number
    asset_ref_norm = models.CharField(max_length=80, editable=False)
    declared_value = models.DecimalField(max_digits=15, decimal_places=2)
    requested_amount = models.DecimalField(max_digits=15, decimal_places=2)
    purpose = models.CharField(max_length=300)
    status = models.CharField(max_length=10, choices=STATUS, default="pending")

    # what staff verified and offered
    verified_value = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    advance_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    advance_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    fee_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    fee_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    share_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)   # Klosa's share of each sale
    term_days = models.PositiveSmallIntegerField(null=True, blank=True)
    assessment_note = models.CharField(max_length=500, blank=True)
    assessed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    assessed_at = models.DateTimeField(null=True, blank=True)

    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    decision_reason = models.CharField(max_length=500, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    offer_expires_at = models.DateTimeField(null=True, blank=True)

    accepted_at = models.DateTimeField(null=True, blank=True)
    maturity_date = models.DateField(null=True, blank=True)

    released_total = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    recovered_principal = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    recovered_fee = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("assess_assetapplication", "Can value assets and prepare an offer"),
            ("approve_assetapplication", "Can approve or reject an asset offer"),
            ("release_advancestage", "Can release an advance stage"),
            ("confirm_assetsale", "Can confirm a reported sale"),
        ]
        indexes = [models.Index(fields=["status"]), models.Index(fields=["user", "status"])]
        constraints = [
            models.UniqueConstraint(fields=["kind", "asset_ref_norm"], condition=Q(status__in=LIVE),
                                    name="one_live_pledge_per_asset"),
            models.CheckConstraint(condition=Q(declared_value__gt=0) & Q(requested_amount__gt=0),
                                   name="asset_amounts_positive"),
            models.CheckConstraint(condition=Q(recovered_principal__lte=F("released_total")),
                                   name="asset_recovered_within_released"),
            models.CheckConstraint(condition=Q(advance_amount__isnull=True) | Q(released_total__lte=F("advance_amount")),
                                   name="asset_released_within_advance"),
        ]

    def save(self, *args, **kwargs):
        self.asset_ref_norm = norm_ref(self.asset_ref)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"

    @property
    def fee_due(self):
        """The fee grows with each stage released, so a customer who stops early pays less."""
        if not self.advance_amount or not self.fee_amount:
            return ZERO
        return money(self.fee_amount * self.released_total / self.advance_amount)

    @property
    def principal_outstanding(self):
        return self.released_total - self.recovered_principal

    @property
    def fee_outstanding(self):
        return self.fee_due - self.recovered_fee

    @property
    def outstanding(self):
        return self.principal_outstanding + self.fee_outstanding


class AdvanceStage(models.Model):
    STATUS = [("planned", "Planned"), ("requested", "Evidence sent"), ("released", "Released"),
              ("cancelled", "Cancelled")]

    application = models.ForeignKey(AssetApplication, on_delete=models.PROTECT, related_name="stages")
    order = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=120)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUS, default="planned")
    evidence_note = models.CharField(max_length=500, blank=True)
    requested_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["application", "order"]
        constraints = [
            models.UniqueConstraint(fields=["application", "order"], name="one_stage_per_order"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="stage_amount_positive"),
        ]


class SaleRecord(models.Model):
    """A sale of the asset (or what was made from it). Klosa's share is taken only after staff confirm it."""
    STATUS = [("pending", "Waiting for confirmation"), ("confirmed", "Confirmed"), ("rejected", "Rejected")]

    application = models.ForeignKey(AssetApplication, on_delete=models.PROTECT, related_name="sales")
    reference = models.CharField(max_length=20, unique=True)
    proceeds = models.DecimalField(max_digits=15, decimal_places=2)
    note = models.CharField(max_length=300)
    is_final = models.BooleanField(default=False)                  # the last sale: unreleased stages are cancelled
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    confirmed_proceeds = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    share_due = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    applied_principal = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    applied_fee = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    shortfall = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)   # due but the balance was short
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=300, blank=True)
    reported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-reported_at"]
        constraints = [models.CheckConstraint(condition=Q(proceeds__gt=0), name="sale_proceeds_positive")]
