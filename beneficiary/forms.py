import re

from django import forms

from accounts.models import Account
from .banks import KLOSA, bank_choices, bank_code_for
from .models import Beneficiary

MAX_BENEFICIARIES = 50
NAME_RE = re.compile(r"[\w .,'’&()\-]{2,100}")


class BeneficiaryForm(forms.Form):
    name = forms.CharField(max_length=100, required=False)
    account_number = forms.CharField(max_length=10, min_length=10)
    bank_name = forms.ChoiceField(choices=bank_choices)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.holder = None            # the real account holder's name for Klosa accounts

    def clean_name(self):
        name = " ".join(self.cleaned_data.get("name", "").split())
        if name and not NAME_RE.fullmatch(name):
            raise forms.ValidationError("Use letters, numbers and spaces only.")
        return name

    def clean_account_number(self):
        number = self.cleaned_data["account_number"].strip()
        if not re.fullmatch(r"\d{10}", number):
            raise forms.ValidationError("Enter a valid 10-digit Nigerian account number.")
        return number

    def clean(self):
        data = super().clean()
        if self.user and Beneficiary.objects.filter(owner=self.user, is_active=True).count() >= MAX_BENEFICIARIES:
            raise forms.ValidationError(f"You can save up to {MAX_BENEFICIARIES} recipients. Remove one first.")
        bank, num = data.get("bank_name"), data.get("account_number")
        if not (bank and num):
            return data

        code = bank_code_for(bank)
        if not code:
            self.add_error("bank_name", "Choose a bank from the list.")
            return data
        data["bank_code"] = code

        if Beneficiary.objects.filter(owner=self.user, bank_code=code, account_number=num, is_active=True).exists():
            self.add_error("account_number", "This recipient is already in your list.")

        if bank == KLOSA:
            target = (Account.objects.select_related("user")
                      .filter(account_number=num, closed_at__isnull=True).first())
            if not target:
                self.add_error("account_number", "No open Klosa account has this number.")
            elif target.user_id == self.user.pk:
                self.add_error("account_number", "You can't add your own account.")
            else:
                self.holder = target.user.get_full_name().strip() or "Klosa customer"
        elif not data.get("name"):
            self.add_error("name", "Enter the recipient's name.")
        return data