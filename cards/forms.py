
from django import forms

from .models import CardOrder


class CardOrderForm(forms.ModelForm):

    class Meta:
        model = CardOrder

        fields = [
            "card_type",
            "card_format",
            "cardholder_name",
            "delivery_address",
            "delivery_city",
            "delivery_state",
            "delivery_phone",
        ]

        widgets = {
            "card_type": forms.Select(),

            "card_format": forms.Select(),

            "cardholder_name": forms.TextInput(attrs={
                "placeholder": "Name to appear on card",
                "autocomplete": "cc-name",
                "maxlength": "100",
            }),

            "delivery_address": forms.Textarea(attrs={
                "placeholder": "Delivery address",
                "rows": 3,
            }),

            "delivery_city": forms.TextInput(attrs={
                "placeholder": "City",
            }),

            "delivery_state": forms.TextInput(attrs={
                "placeholder": "State",
            }),

            "delivery_phone": forms.TextInput(attrs={
                "placeholder": "Delivery contact number",
                "inputmode": "tel",
            }),
        }

    def clean_cardholder_name(self):
        name = self.cleaned_data["cardholder_name"].strip()

        if not name:
            raise forms.ValidationError(
                "Enter the cardholder's name."
            )

        return name

    def clean(self):
        cleaned_data = super().clean()

        card_format = cleaned_data.get("card_format")

        delivery_fields = [
            "delivery_address",
            "delivery_city",
            "delivery_state",
            "delivery_phone",
        ]

        if card_format == "physical":
            for field in delivery_fields:
                if not cleaned_data.get(field):
                    self.add_error(
                        field,
                        "This field is required for physical cards."
                    )

        elif card_format == "virtual":
            for field in delivery_fields:
                cleaned_data[field] = ""

        return cleaned_data
