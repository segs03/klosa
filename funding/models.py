
from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import Q
from django.core.exceptions import ValidationError

ZERO = Decimal("0.00")
HUNDRED = Decimal("100.00")


class Investor(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="investor_profile",
    )
    legal_name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.legal_name


class CapitalContribution(models.Model):
    SOURCE_CHOICES = [
        ("company", "Company capital"),
        ("investor", "Investor capital"),
    ]

    source_type = models.CharField(max_length=10, choices=SOURCE_CHOICES)
    investor = models.ForeignKey(
        Investor,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="contributions",
    )
    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    reference = models.CharField(max_length=100, unique=True)
    received_at = models.DateTimeField()
    verified = models.BooleanField(default=False)
    
    verified_by = models.ForeignKey(
    settings.AUTH_USER_MODEL,
    null=True,
    blank=True,
    on_delete=models.PROTECT,
    related_name="verified_capital_contributions",
)

    verified_at = models.DateTimeField(null=True, blank=True)
    journal_reference = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(source_type="company", investor__isnull=True)
                    | Q(source_type="investor", investor__isnull=False)
                ),
                name="capital_source_investor_consistent",
            ),
        ]

    def __str__(self):
        return f"{self.source_type}: {self.amount}"


class InvestorAgreement(models.Model):
    """
    The agreed allocation terms for an investor.

    Capital component:
      share is calculated from eligible contributed capital.

    Agreed component:
      share is taken from the agreed percentage fields.

    The percentages are separate for profit and loss.
    """
    
    CAPITAL_BASIS_CHOICES = [
    ("opening_balance", "Opening balance"),
    ("time_weighted_average", "Time-weighted average"),
]

    PROFIT_TREATMENT_CHOICES = [
    ("payout", "Pay out"),
    ("reinvest", "Reinvest"),
    ("retain", "Retain"),
]

    capital_basis = models.CharField(
    max_length=30,
    choices=CAPITAL_BASIS_CHOICES,
    default="time_weighted_average",
)

    profit_treatment = models.CharField(
    max_length=20,
                                                        choices=PROFIT_TREATMENT_CHOICES,
    default="retain",
)
    
    investor = models.ForeignKey(
        Investor,
        on_delete=models.PROTECT,
        related_name="agreements",
    )
    
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)

    profit_capital_component = models.DecimalField(
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(ZERO), MaxValueValidator(HUNDRED)],
    )
    profit_agreed_component = models.DecimalField(
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(ZERO), MaxValueValidator(HUNDRED)],
    )
    agreed_profit_share = models.DecimalField(
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(ZERO), MaxValueValidator(HUNDRED)],
    )

    loss_capital_component = models.DecimalField(
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(ZERO), MaxValueValidator(HUNDRED)],
    )
    loss_agreed_component = models.DecimalField(
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(ZERO), MaxValueValidator(HUNDRED)],
    )
    agreed_loss_share = models.DecimalField(
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(ZERO), MaxValueValidator(HUNDRED)],
    )

    signed_document_reference = models.CharField(max_length=255)
    approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True) |
                          Q(effective_to__gte=models.F("effective_from")),
                name="agreement_dates_valid",
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.profit_capital_component + self.profit_agreed_component != HUNDRED:
            raise ValidationError(
                "Profit allocation components must total 100%."
            )

        if self.loss_capital_component + self.loss_agreed_component != HUNDRED:
            raise ValidationError(
                "Loss allocation components must total 100%."
            )


class FundingPeriod(models.Model):
    STATUS_CHOICES = [
        ("open", "Open"),
        ("review", "Under review"),
        ("closed", "Closed"),
    ]

    start_date = models.DateField(unique=True)
    end_date = models.DateField()
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="open"
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        permissions = [
    (
        "review_fundingperiod",
        "Can move funding periods into review",
    ),
    (
        "close_fundingperiod",
        "Can close funding periods",
    ),
    (
        "reopen_fundingperiod",
        "Can reopen closed funding periods",
    ),
]
        ordering = ["-start_date"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=models.F("start_date")),
                name="funding_period_dates_valid",
            ),
        ]

    def __str__(self):
        return f"{self.start_date:%B %Y}"


class FundingPeriodReopening(models.Model):
    period = models.ForeignKey(
        FundingPeriod,
        on_delete=models.PROTECT,
        related_name="reopenings",
    )
    reopened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reopened_funding_periods",
    )
    reason = models.CharField(max_length=255)
    previous_closed_at = models.DateTimeField()
    reopened_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.period} reopened by {self.reopened_by}"


class InvestorAllocation(models.Model):
    TYPE_CHOICES = [
        ("profit", "Profit"),
        ("loss", "Loss"),
    ]

    period = models.ForeignKey(
        FundingPeriod, on_delete=models.PROTECT, related_name="allocations"
    )
    investor = models.ForeignKey(
        Investor, on_delete=models.PROTECT, related_name="allocations"
    )
    allocation_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    capital_component = models.DecimalField(
        max_digits=15, decimal_places=2, default=ZERO
    )
    agreed_component = models.DecimalField(
        max_digits=15, decimal_places=2, default=ZERO
    )
    agreement = models.ForeignKey(
        InvestorAgreement, on_delete=models.PROTECT
    )
    
    approval_status = models.CharField(
    max_length=10,
    choices=[
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ],
    default="pending",
)

    approved_by = models.ForeignKey(
    settings.AUTH_USER_MODEL,
    null=True,
    blank=True,
    on_delete=models.PROTECT,
    related_name="approved_investor_allocations",
)

    approved_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    def _check_allocation_editable(self):
        if not self.pk:
            return

        old_status = type(self).objects.values_list(
        "approval_status", flat=True
    ).get(pk=self.pk)

        if old_status == "approved":
            raise ValidationError(
            "Approved allocations cannot be edited."
        )

        if self.capital_entries.exists():
            raise ValidationError(
            "Allocations linked to capital entries cannot be edited."
        )


    def save(self, *args, **kwargs):
        self._check_allocation_editable()
        super().save(*args, **kwargs)


    def delete(self, *args, **kwargs):
        self._check_allocation_editable()
        return super().delete(*args, **kwargs)
    
    def clean(self):
        super().clean()

        if self.approval_status == "approved":
            if not self.approved_by_id or not self.approved_at:
                raise ValidationError(
                "Approved allocations require an approver and approval date."
            )

        if self.approval_status != "approved":
            if self.approved_by_id or self.approved_at:
                raise ValidationError(
                "Only approved allocations can have approval details."
            )

        if self.agreement_id and self.investor_id:
            if self.agreement.investor_id != self.investor_id:
                raise ValidationError(
                "The agreement does not belong to this investor."
            )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["period", "investor", "allocation_type"],
                name="unique_investor_allocation_per_period_type",
            ),
        ]
        
        permissions = [
        (
            "approve_investorallocation",
            "Can approve investor allocations",
        ),
    ]


class LedgerAccount(models.Model):
    """
    Examples:
    1000 - Operating cash
    1100 - Credit principal receivable
    2000 - Investor capital
    2100 - Company capital
    3000 - Retained earnings
    4000 - Credit income
    5000 - Operating expenses
    5100 - Credit loss expense
    """
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    account_type = models.CharField(
        max_length=20,
        choices=[
            ("asset", "Asset"),
            ("liability", "Liability"),
            ("equity", "Equity"),
            ("income", "Income"),
            ("expense", "Expense"),
        ],
    )
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.code} - {self.name}"


class JournalEntry(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("posted", "Posted"),
        ("reversed", "Reversed"),
    ]

    reference = models.CharField(max_length=100, unique=True)
    period = models.ForeignKey(
        FundingPeriod, on_delete=models.PROTECT, related_name="journal_entries"
    )
    description = models.CharField(max_length=255)
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="draft"
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    
    reversal_of = models.OneToOneField(
    "self",
    null=True,
    blank=True,
    on_delete=models.PROTECT,
    related_name="reversal_entry",
)

    reversal_reason = models.CharField(
    max_length=255,
    blank=True,
)

    reversed_by = models.ForeignKey(
    settings.AUTH_USER_MODEL,
    null=True,
    blank=True,
    on_delete=models.PROTECT,
    related_name="reversed_journals",
)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    def save(self, *args, **kwargs):
        if self.pk:
            old = JournalEntry.objects.get(pk=self.pk)

            if old.status in ("posted", "reversed"):
                allowed_reversal = (
                old.status == "posted"
                and self.status == "reversed"
                and old.reference == self.reference
                and old.period_id == self.period_id
                and old.description == self.description
                and old.posted_at == self.posted_at
                and old.reversal_of_id == self.reversal_of_id
                and old.reversal_reason == self.reversal_reason
                and old.reversed_by_id == self.reversed_by_id
            )

                if not allowed_reversal:
                    raise ValidationError(
                    "Posted or reversed journal entries cannot be edited."
                )

        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status in ("posted", "reversed"):
            raise ValidationError(
                "Posted or reversed journal entries cannot be deleted."
            )

        return super().delete(*args, **kwargs)
    
    class Meta:
        constraints = [
        models.CheckConstraint(
            condition=(
                Q(reversal_of__isnull=True)
                | (
                    Q(reversal_reason__gt="")
                    & Q(reversed_by__isnull=False)
                )
            ),
                    name="journal_reversal_requires_reason_and_user",
        ),
    ]
    
    def __str__(self):
        return self.reference


class JournalLine(models.Model):
    entry = models.ForeignKey(
        JournalEntry, on_delete=models.PROTECT, related_name="lines"
    )
    account = models.ForeignKey(
        LedgerAccount, on_delete=models.PROTECT, related_name="journal_lines"
    )
    debit = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    credit = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(debit__gte=ZERO) & Q(credit__gte=ZERO)
                    & (
                        (Q(debit__gt=ZERO) & Q(credit=ZERO))
                        | (Q(credit__gt=ZERO) & Q(debit=ZERO))
                    )
                ),
                name="journal_line_one_positive_side",
            ),
        ]
        
    def _check_entry_editable(self):
        if self.entry_id:
            status = JournalEntry.objects.values_list(
                "status", flat=True
            ).get(pk=self.entry_id)

            if status != "draft":
                raise ValidationError(
                    "Journal lines cannot be changed after posting."
                )

    def save(self, *args, **kwargs):
        self._check_entry_editable()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._check_entry_editable()
        return super().delete(*args, **kwargs)
        
        
        


class InvestorCapitalEntry(models.Model):
    ENTRY_TYPES = [
        ("contribution", "Capital contribution"),
        ("withdrawal", "Capital withdrawal"),
        ("reinvestment", "Profit reinvestment"),
        ("retained_profit", "Retained profit"),
        ("payout", "Profit payout"),
        ("loss", "Allocated investment loss"),
    ]

    investor = models.ForeignKey(
        Investor,
        on_delete=models.PROTECT,
        related_name="capital_entries",
    )
    entry_type = models.CharField(
        max_length=20,
        choices=ENTRY_TYPES,
    )
    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    effective_date = models.DateField()
    reference = models.CharField(max_length=100, unique=True)
    
    source_contribution = models.OneToOneField(
    CapitalContribution,
    null=True,
    blank=True,
    on_delete=models.PROTECT,
    related_name="capital_entry",
)
    
    allocation = models.ForeignKey(
        InvestorAllocation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="capital_entries",
    )
    journal_entry = models.ForeignKey(
    JournalEntry,
    on_delete=models.PROTECT,
    related_name="capital_entries",
)

    description = models.CharField(max_length=255, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk and InvestorCapitalEntry.objects.filter(
            pk=self.pk,
            posted_at__isnull=False,
        ).exists():
            raise ValidationError(
                "Posted capital entries cannot be edited."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.posted_at:
            raise ValidationError(
                "Posted capital entries cannot be deleted."
            )
        super().delete(*args, **kwargs)

    class Meta:
        ordering = ["effective_date", "created_at"]

        constraints = [
            models.UniqueConstraint(
            fields=["allocation"],
            condition=Q(allocation__isnull=False),
            name="unique_capital_entry_per_allocation",
        ),
    ]

    indexes = [
        models.Index(
            fields=["investor", "effective_date"],
            name="investor_capital_date_idx",
        ),
    ]

    def __str__(self):
        return (
            f"{self.investor.legal_name} - "
            f"{self.get_entry_type_display()} - {self.amount}"
        )

