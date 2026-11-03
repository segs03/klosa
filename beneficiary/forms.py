
import re
from django import forms


class BeneficiaryForm(forms.Form):
    name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Account holder's name",
        })
    )

    account_number = forms.CharField(
        max_length=10,
        min_length=10,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Enter 10-digit account number",
            "inputmode": "numeric",
            "autocomplete": "off",
        })
    )

    bank_name = forms.ChoiceField(
        choices=[
            ("", "Select bank"),
            ("Access Bank", "Access Bank"),
            ("Citibank Nigeria", "Citibank Nigeria"),
            ("Ecobank Nigeria", "Ecobank Nigeria"),
            ("Fidelity Bank", "Fidelity Bank"),
            ("First Bank", "First Bank"),
            ("FCMB", "FCMB"),
            ("GTBank", "GTBank"),
            ("Keystone Bank", "Keystone Bank"),
            ("Stanbic IBTC", "Stanbic IBTC"),
            ("Sterling Bank", "Sterling Bank"),
            ("UBA", "UBA"),
            ("Union Bank", "Union Bank"),
            ("Unity Bank", "Unity Bank"),
            ("Wema Bank", "Wema Bank"),
            ("Zenith Bank", "Zenith Bank"),
        ],
        widget=forms.Select(attrs={
            "class": "form-control bank-select",
        })
    )

    def clean_name(self):
        name = self.cleaned_data["name"].strip()

        if not name:
            raise forms.ValidationError(
                "Enter the beneficiary's name."
            )

        return name

    def clean_account_number(self):
        number = self.cleaned_data["account_number"].strip()

        if not re.fullmatch(r"\d{10}", number):
            raise forms.ValidationError(
                "Enter a valid 10-digit Nigerian account number."
            )

        return number
