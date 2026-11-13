
import re

from django import forms
from django.conf import settings
from accounts.models import Account
from .banks import KLOSA, bank_choices, bank_code_for
from .models import Beneficiary


class BeneficiaryForm(forms.Form):
    name = forms.CharField(
        max_length=100, required=False,           # filled in automatically for Klosa accounts
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Account holder's name"}))

    account_number = forms.CharField(
        max_length=10, min_length=10,
        widget=forms.TextInput(attrs={
            "class": "form-control", "placeholder": "Enter 10-digit account number",
            "inputmode": "numeric", "autocomplete": "off"}))

    bank_name = forms.ChoiceField(
        choices=bank_choices,
        widget=forms.Select(attrs={"class": "form-control bank-select"}))

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.holder = None            # set when the beneficiary is a Klosa account

    def clean_name(self):
        return " ".join(self.cleaned_data.get("name", "").split())

    def clean_account_number(self):
        number = self.cleaned_data["account_number"].strip()
        if not re.fullmatch(r"\d{10}", number):
            raise forms.ValidationError("Enter a valid 10-digit Nigerian account number.")
        return number

    def clean(self):
        data = super().clean()
        bank, num = data.get("bank_name"), data.get("account_number")
        if not (bank and num):
            return data

        code = bank_code_for(bank)
        if not code:
            self.add_error("bank_name", "Choose a bank from the list.")
            return data
        data["bank_code"] = code                       # the view reads this

        if Beneficiary.objects.filter(owner=self.user, bank_code=code,
                                      account_number=num, is_active=True).exists():
            self.add_error("account_number", "This beneficiary is already in your list.")

        if bank == KLOSA:
            target = (Account.objects.select_related("user")
                      .filter(account_number=num, closed_at__isnull=True).first())
            if not target:
                self.add_error("account_number", "No open Klosa account has this number.")
            elif target.user_id == self.user.pk:
                self.add_error("account_number", "You can't add your own account.")
            else:
                # use the real holder's name, never what the user typed
                self.holder = target.user.get_full_name().strip() or "Klosa customer"
        elif not data.get("name"):
            self.add_error("name", "Enter the beneficiary's name.")
        return data