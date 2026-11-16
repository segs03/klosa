import re

from django import forms

from beneficiary.banks import KLOSA, bank_choices
from . import rules
from .services import CashierError, clean_amount


class DepositForm(forms.Form):
    amount = forms.CharField(max_length=15)

    def clean_amount(self):
        try:
            return clean_amount(self.cleaned_data["amount"], rules.DEPOSIT_MIN, rules.DEPOSIT_MAX)
        except CashierError as e:
            raise forms.ValidationError(str(e))


class WithdrawForm(forms.Form):
    bank_name = forms.ChoiceField(choices=[])
    account_number = forms.CharField(max_length=10)
    amount = forms.CharField(max_length=15)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bank_name"].choices = [c for c in bank_choices() if c[0] != KLOSA]

    def clean_account_number(self):
        n = self.cleaned_data["account_number"].strip()
        if not re.fullmatch(r"\d{10}", n):
            raise forms.ValidationError("Enter a valid 10-digit account number.")
        return n

    def clean_amount(self):
        try:
            return clean_amount(self.cleaned_data["amount"], rules.WITHDRAW_MIN, rules.WITHDRAW_MAX)
        except CashierError as e:
            raise forms.ValidationError(str(e))