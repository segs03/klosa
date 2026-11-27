from django import forms

from .models import AssetApplication


class ApplyForm(forms.Form):
    """Everything is checked again in services.submit_application, which is the single source of the rules."""

    kind = forms.ChoiceField(choices=AssetApplication.KINDS, label="What is it?")
    title = forms.CharField(max_length=120, label="Give it a name",
                            widget=forms.TextInput(attrs={"placeholder": "e.g. Raw gold for jewellery"}))
    description = forms.CharField(max_length=1000, label="Describe it (weight, size, condition, papers)",
                                  widget=forms.Textarea(attrs={"rows": 4}))
    asset_ref = forms.CharField(max_length=80, label="Serial, title deed, assay or contract number")
    declared_value = forms.CharField(max_length=15, label="What you think it is worth (₦)",
                                     widget=forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off"}))
    requested_amount = forms.CharField(max_length=15, label="Amount you need (₦)",
                                       widget=forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off"}))
    purpose = forms.CharField(max_length=300, label="What will the money be used for?",
                              widget=forms.TextInput(attrs={"placeholder": "e.g. Mould the gold into jewellery for an order"}))
