
from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError
from beneficiary.models import Beneficiary


SINGLE_LIMIT = Decimal("500000.00")
DAILY_LIMIT = Decimal("1000000.00")


class BeneficiaryChoiceField(forms.ModelChoiceField):

    def label_from_instance(self, beneficiary):
        return (
            f"{beneficiary.name} · "
            f"{beneficiary.bank_name} · "
            f"****{beneficiary.account_number[-4:]}"
        )


class TransferForm(forms.Form):

    account_number = forms.CharField(
        label="Account number",
        required=False,
        max_length=10,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Enter account number",
            "inputmode": "numeric",
            "autocomplete": "off",
        })
    )

    beneficiary = BeneficiaryChoiceField(
        queryset=Beneficiary.objects.none(),
        required=False,
        empty_label="Select saved beneficiary",
        widget=forms.Select(attrs={
            "class": "form-control select",
        })
    )

    amount = forms.DecimalField(
        label="Amount",
        max_digits=15,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "placeholder": "0.00",
            "step": "0.01",
            "min": "0.01",
            "inputmode": "decimal",
        })
    )

    #description = forms.CharField(
     # label="Description",
     #   required=False,
     #   max_length=255,
      #    widget=forms.TextInput(attrs={
      #       "class": "form-control",
       #     "placeholder": "What's this transfer for?",
     #   })
 #   )

    pin = forms.CharField(
        label="Transaction PIN",
        min_length=4,
        max_length=4,
        strip=False,
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Enter 4-digit PIN",
            "inputmode": "numeric",
            "autocomplete": "off",
        })
    )

    def __init__(
        self,
        *args,
        account=None,
        used_today=Decimal("0.00"),
        **kwargs
    ):
        super().__init__(*args, **kwargs)

        self.account = account
        self.used_today = Decimal(str(used_today))

        if account:
            self.fields["beneficiary"].queryset = (
                Beneficiary.objects.filter(
                    owner=account.user,
                    is_active=True,
                ).order_by("name")
            )

    # -------------------------
    # Recipient validation
    # -------------------------

    def clean_account_number(self):
        number = self.cleaned_data["account_number"].strip()

        if number and (
            not number.isdigit() or len(number) != 10
        ):
            raise ValidationError(
                "Enter a valid 10-digit account number."
            )

        return number

    def clean(self):
        cleaned_data = super().clean()

        account_number = cleaned_data.get("account_number")
        beneficiary = cleaned_data.get("beneficiary")

        if not account_number and not beneficiary:
            raise ValidationError(
                "Enter an account number or select a beneficiary."
            )

        if account_number and beneficiary:
            raise ValidationError(
                "Choose only one recipient method."
            )
            
        if acc.closed_at:
            raise forms.ValidationError("This account is closed.")

        if not self.account:
            return cleaned_data

        recipient_number = (
            account_number
            or (
                beneficiary.account_number
                if beneficiary else None
            )
        )

        if recipient_number == self.account.account_number:
            self.add_error(
                "account_number" if account_number else "beneficiary",
                "You cannot transfer to your own account."
            )

        return cleaned_data

    # -------------------------
    # Amount validation
    # -------------------------

    def clean_amount(self):
        amount = self.cleaned_data["amount"]

        if amount > SINGLE_LIMIT:
            raise ValidationError(
                f"Maximum transfer amount is ₦{SINGLE_LIMIT:,.2f}."
            )

        if self.account:
            if amount > self.account.balance:
                raise ValidationError("Insufficient balance.")

            if amount + self.used_today > DAILY_LIMIT:
                remaining = max(
                    DAILY_LIMIT - self.used_today,
                    Decimal("0.00")
                )

                raise ValidationError(
                    f"Daily limit exceeded. "
                    f"Remaining limit: ₦{remaining:,.2f}."
                )

        return amount

    # -------------------------
    # PIN format validation
    # -------------------------

    def clean_pin(self):
        pin = self.cleaned_data["pin"]

        if not pin.isascii() or not pin.isdigit():
            raise ValidationError(
                "PIN must contain exactly 4 digits."
            )

        return pin

    @property
    def recipient_account_number(self):
        """Return the resolved recipient account number."""
        if not self.is_valid():
            return None

        return (
            self.cleaned_data.get("account_number")
            or self.cleaned_data["beneficiary"].account_number
        )

        

class RequestForm(forms.Form):

    account_number = forms.CharField(  
    max_length=10,  
    label="Recipient account number",  
    widget=forms.TextInput(attrs={  
    'class': 'form-control', 'id': 'account_number', 'placeholder': 'Account Number'  
    })  
)  

    amount = forms.DecimalField(  
    max_digits=15,  
    decimal_places=2,  
    min_value=0.01,  
    widget=forms.NumberInput(attrs={  
    'class': 'form-control', 'id': 'amount', 'placeholder': '0.00', 'step': '0.01', 'min': '0.01', })  
)  
  
    currency = forms.ChoiceField(  
label = "currency",  
choices = [("ngn", " NGN"), ("usd", "USD"),("eur", "EUR"), ("aud","AUD")  
],  
widget=forms.Select(attrs={  
'class': 'currency radius-lg'}),  
)  
  
    note = forms.CharField(  
    required= False,  
    label = "Notes",  
    max_length = 100,  
    widget = forms.Textarea(attrs={  
    'class': 'form-control note-field', 'placeholder': 'Add note',   
    }),  
)
