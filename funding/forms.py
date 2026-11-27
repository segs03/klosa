from django import forms


class PeriodReopeningForm(forms.Form):
    reason = forms.CharField(
        max_length=255,
        min_length=5,
        widget=forms.Textarea(attrs={
            "rows": 4,
            "placeholder": "Explain why this funding period must be reopened.",
        }),
    )


class AllocationProposalForm(forms.Form):
    result = forms.DecimalField(
        max_digits=15,
        decimal_places=2,
        label="Approved result for the period (₦)",
        help_text="Positive for a profit to share, negative for a loss to share.",
        widget=forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off", "placeholder": "e.g. 250000.00 or -40000.00"}),
    )
