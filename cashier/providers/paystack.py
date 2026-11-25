import json
import urllib.error
import urllib.request
from decimal import Decimal

from django.conf import settings
from django.urls import reverse

from .base import BaseCashProvider, Result

API = "https://api.paystack.co"


def _call(method, path, payload=None):
    req = urllib.request.Request(
        API + path, method=method, data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": "Bearer " + settings.PAYSTACK_SECRET_KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Paystack replied {e.code}")


class PaystackProvider(BaseCashProvider):
    def start_checkout(self, deposit):
        acc = deposit.account
        email = acc.user.email or f"{acc.account_number}@{settings.CASHIER_FALLBACK_EMAIL_DOMAIN}"
        data = _call("POST", "/transaction/initialize", {
                    "channels": ["card"] if deposit.method == "card" else ["bank", "bank_transfer"],
            "email": email, "currency": "NGN", "reference": deposit.reference,
            "amount": int((deposit.amount * 100).to_integral_value()),               # kobo
            "callback_url": settings.SITE_URL.rstrip("/") + reverse("cashier:deposit_status", args=[deposit.reference])})
        if not data.get("status"):
            raise RuntimeError("Paystack refused the payment")
        d = data["data"]
        return d["authorization_url"], d.get("reference", deposit.reference)

    def verify_deposit(self, deposit):
        d = _call("GET", "/transaction/verify/" + deposit.reference).get("data") or {}
        status = d.get("status")
        if status == "success":
            return Result("success", provider_reference=str(d.get("id", "")), amount=Decimal(str(d["amount"])) / 100)
        if status in ("failed", "reversed"):
            return Result("failed")
        return Result("pending")