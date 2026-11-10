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