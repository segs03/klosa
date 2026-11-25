import uuid

from django import forms
from django.conf import settings

from .kyc_forms import SIGNATURES


class AddressForm(forms.Form):
    line1 = forms.CharField(label="Street address", max_length=150)
    line2 = forms.CharField(label="Estate, apartment or landmark (optional)", max_length=150, required=False)
    city = forms.CharField(label="City", max_length=100)
    state = forms.CharField(label="State", max_length=100)
    bill_date = forms.DateField(label="Date on the bill")
    document = forms.FileField(label="Photo or scan of the utility bill")
    consent = forms.BooleanField(label="I confirm this is my home address and the bill is in my name.")

    def clean_document(self):
        f = self.cleaned_data["document"]
        if f.size > getattr(settings, "KYC_MAX_UPLOAD", 5 * 1024 * 1024):
            raise forms.ValidationError("The file is too big. Use one under 5 MB.")
        head = f.read(16)
        f.seek(0)
        ext = next((e for sig, e in SIGNATURES if head.startswith(sig)), None)   # real contents, not the name
        if not ext:
            raise forms.ValidationError("Upload a JPG, PNG or PDF file.")
        f.name = uuid.uuid4().hex + ext                    # never keep the customer's file name
        return f
