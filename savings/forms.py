from decimal import Decimal

from django import forms

from . import rules


def pin_field():
    return forms.CharField(
        label="Transaction PIN", min_length=4, max_length=4, strip=False,
        widget=forms.PasswordInput(attrs={"inputmode": "numeric", "autocomplete": "off"}))


def money_field(label, **kw):
    return forms.DecimalField(
        label=label, max_digits=15, decimal_places=2,
        widget=forms.NumberInput(attrs={"inputmode": "decimal", "step": "0.01", "placeholder": "0.00"}), **kw)


class PinMixin:
    def clean_pin(self):
        pin = self.cleaned_data["pin"]
        if not (pin.isascii() and pin.isdigit()):
            raise forms.ValidationError("PIN must contain exactly 4 digits.")
        return pin


TERMS = [("", "Choose a term")] + [(str(d), f"{d} days · {r}% a year") for d, r in rules.LOCKED_TERMS.items()]


class CreatePlanForm(PinMixin, forms.Form):
    name = forms.CharField(label="Plan name", max_length=40,
                           widget=forms.TextInput(attrs={"placeholder": "For example: New phone"}))
    kind = forms.ChoiceField(label="Plan type", choices=[
        ("flex", f"Flexible · {rules.FLEX_RATE}% a year · withdraw any time"),
        ("locked", "Locked · higher rate · fixed term")])
    term_days = forms.ChoiceField(label="Term (locked plans)", required=False, choices=TERMS)
    target_amount = money_field("Goal amount (optional)", required=False, min_value=Decimal("1"))
    amount = money_field("Amount to save now", required=False, min_value=Decimal("0"))
    pin = pin_field()

    def clean(self):
        d = super().clean()
        if d.get("kind") == "locked":
            if not d.get("term_days"):
                self.add_error("term_days", "Choose a term for a locked plan.")
            if not d.get("amount"):
                self.add_error("amount", "Enter the amount to lock.")
        return d


class MoveForm(PinMixin, forms.Form):
    amount = money_field("Amount", min_value=Decimal("0.01"))
    pin = pin_field()


class PinForm(PinMixin, forms.Form):
    pin = pin_field()