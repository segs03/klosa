import re
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from accounts.services import limits_for
from beneficiary.models import Beneficiary
from django.conf import settings


class BeneficiaryChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, b):
        return f"{b.name} · {b.bank_name} · ****{b.account_number[-4:]}"


class TransferForm(forms.Form):
    account_number = forms.CharField(
        label="Account number", required=False, max_length=10,
        widget=forms.TextInput(attrs={
            "class": "form-control", "placeholder": "Enter account number",
            "inputmode": "numeric", "autocomplete": "off"}))

    beneficiary = BeneficiaryChoiceField(
        queryset=Beneficiary.objects.none(), required=False,
        empty_label="Select saved beneficiary",
        widget=forms.Select(attrs={"class": "form-control select"}))

    amount = forms.DecimalField(
        label="Amount", max_digits=15, decimal_places=2, min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={
            "class": "form-control", "placeholder": "0.00",
            "step": "0.01", "min": "0.01", "inputmode": "decimal"}))

    description = forms.CharField(
        label="Description", required=False, max_length=255,
        widget=forms.TextInput(attrs={
            "class": "form-control", "placeholder": "What's this transfer for?"}))

    pin = forms.CharField(
        label="Transaction PIN", min_length=4, max_length=4, strip=False,
        widget=forms.PasswordInput(attrs={
            "class": "form-control", "placeholder": "Enter 4-digit PIN",
            "inputmode": "numeric", "autocomplete": "off"}))

    def __init__(self, *args, account=None, used_today=Decimal("0.00"), **kwargs):
        super().__init__(*args, **kwargs)
        self.account = account
        self.used_today = Decimal(str(used_today))
        if account:
            self.fields["beneficiary"].queryset = Beneficiary.objects.filter(
                owner=account.user, is_active=True).order_by("name")

    def clean_account_number(self):
        number = self.cleaned_data["account_number"].strip()
        if number and not re.fullmatch(r"\d{10}", number):
            raise ValidationError("Enter a valid 10-digit account number.")
        return number

    def clean_pin(self):
        pin = self.cleaned_data["pin"]
        if not (pin.isascii() and pin.isdigit()):
            raise ValidationError("PIN must contain exactly 4 digits.")
        return pin

    def clean(self):
        data = super().clean()
        number, ben = data.get("account_number"), data.get("beneficiary")

        if not number and not ben:
            raise ValidationError("Enter an account number or select a beneficiary.")
        if number and ben:
            raise ValidationError("Choose only one recipient method.")
        if not self.account:
            return data

        if self.account.closed_at:
            raise ValidationError("This account is closed.")

        target = number or ben.account_number
        if target == self.account.account_number:
            self.add_error("account_number" if number else "beneficiary",
                           "You cannot transfer to your own account.")

        if ben and ben.bank_code != settings.KLOSA_BANK_CODE:
            self.add_error("beneficiary", "Transfers to other banks are not available yet.")

        amount = data.get("amount")
        if amount is not None:
            lim = limits_for(self.account)
            if amount > lim["single"]:
                self.add_error("amount",
                    f"Your limit is ₦{lim['single']:,.0f} per transfer. Verify your ID to raise it.")
            elif amount + self.used_today > lim["daily"]:
                left = max(lim["daily"] - self.used_today, Decimal("0.00"))
                self.add_error("amount",
                    f"Daily limit reached. You can send ₦{left:,.2f} more today.")
            elif amount > self.account.balance:
                self.add_error("amount", "Insufficient balance.")
        return data


class RequestForm(forms.Form):
    account_number = forms.CharField(
        max_length=10, label="Recipient account number",
        widget=forms.TextInput(attrs={
            "class": "form-control", "id": "account_number", "placeholder": "Account Number",
            "inputmode": "numeric", "autocomplete": "off"}))

    amount = forms.DecimalField(
        max_digits=15, decimal_places=2, min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={
            "class": "form-control", "id": "amount", "placeholder": "0.00",
            "step": "0.01", "min": "0.01", "inputmode": "decimal"}))

    note = forms.CharField(
        required=False, label="Notes", max_length=100,
        widget=forms.TextInput(attrs={
            "class": "form-control note-field", "placeholder": "Add note", "maxlength": 100}))

    def clean_account_number(self):
        number = self.cleaned_data["account_number"].strip()
        if not re.fullmatch(r"\d{10}", number):
            raise ValidationError("Enter a valid 10-digit account number.")
        return number

    def clean_note(self):
        return " ".join(self.cleaned_data.get("note", "").split())