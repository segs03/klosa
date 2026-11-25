import uuid

from django import forms
from django.conf import settings

from .kyc_services import DOC_TYPES, clean_id_number

SIGNATURES = ((b"\xff\xd8\xff", ".jpg"), (b"\x89PNG\r\n\x1a\n", ".png"), (b"%PDF-", ".pdf"))


class KYCForm(forms.Form):
    doc_type = forms.ChoiceField(label="ID type", choices=[("", "Choose an ID")] + [(k, v[0]) for k, v in DOC_TYPES.items()])
    id_number = forms.CharField(label="ID number", max_length=30)
    legal_name = forms.CharField(label="Full legal name", max_length=150)
    date_of_birth = forms.DateField(label="Date of birth")
    document = forms.FileField(label="Photo or scan of the ID")
    consent = forms.BooleanField(label="I agree that Klosa may check my ID and keep it as the law requires.")

    def clean_document(self):
        f = self.cleaned_data["document"]
        if f.size > getattr(settings, "KYC_MAX_UPLOAD", 5 * 1024 * 1024):
            raise forms.ValidationError("The file is too big. Use one under 5 MB.")
        head = f.read(16)
        f.seek(0)
        ext = next((e for sig, e in SIGNATURES if head.startswith(sig)), None)   # check the real contents, not the name
        if not ext:
            raise forms.ValidationError("Upload a JPG, PNG or PDF file.")
        f.name = uuid.uuid4().hex + ext                   # never keep the customer's file name
        return f


class BVNForm(forms.Form):
    """BVN has no card or paper, so it needs only the number and consent."""
    id_number = forms.CharField(label="BVN", max_length=30)
    consent = forms.BooleanField(label="I agree that Klosa may check my BVN with the bank verification service.")

    def clean_id_number(self):
        return clean_id_number("bvn", self.cleaned_data["id_number"])
