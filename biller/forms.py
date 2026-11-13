from django import forms

from . import services as svc
from .rules import FIXED_PRICE, RULES


class DetailsForm(forms.Form):
    provider = forms.ChoiceField(choices=[])
    product = forms.CharField(required=False, max_length=60)
    customer_ref = forms.CharField(max_length=30)
    amount = forms.CharField(required=False, max_length=15)

    def __init__(self, *args, category, provider, label, **kwargs):
        super().__init__(*args, **kwargs)
        self.category = category
        self.rule = RULES[category]
        self.backend = provider
        self.catalog = provider.catalog(category)
        self.names = {p["code"]: p["name"] for p in self.catalog["providers"]}
        self.fields["provider"].choices = list(self.names.items())
        self.fields["customer_ref"].label = label
        self.provider_name = self.product_name = ""

    def clean_customer_ref(self):
        try:
            return svc.clean_customer_ref(self.category, self.cleaned_data["customer_ref"])
        except svc.PaymentError as e:
            raise forms.ValidationError(str(e))

    def clean(self):
        d = super().clean()
        prov = d.get("provider")
        if not prov:
            return d
        self.provider_name = self.names.get(prov, "")
        try:
            if self.rule["product"]:
                code = (d.get("product") or "").strip()
                match = next((p for p in self.catalog["products"].get(prov, []) if p["code"] == code), None)
                if not match:
                    raise svc.PaymentError("Choose a plan.")
                d["product"], self.product_name = code, match["name"]
            else:
                d["product"] = ""
            if self.category in FIXED_PRICE:
                price = self.backend.price_of(self.category, prov, d["product"])
                if price is None:
                    raise svc.PaymentError("That plan is not available.")
                d["amount"] = price                              # the provider sets the price
            else:
                amount = svc.clean_amount(d.get("amount"))
                lo, hi = self.rule["min"], self.rule["max"]
                if amount < lo or amount > hi:
                    raise svc.PaymentError(f"Amount must be between ₦{lo:,.0f} and ₦{hi:,.0f}.")
                d["amount"] = amount
        except svc.PaymentError as e:
            self.add_error("product" if self.rule["product"] else "amount", str(e))
        return d