import hashlib
import hmac
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import Account
from cashier.services import start_deposit

User = get_user_model()
KEY = "sk_test_abc"


@override_settings(CASHIER_PROVIDER="cashier.providers.mock.MockCashProvider", PAYSTACK_SECRET_KEY=KEY)
class PaystackWebhookTests(TestCase):
    def setUp(self):
        cache.clear()
        u = User.objects.create_user("ann", password="x")
        self.acc = Account.objects.create(user=u, account_number="1000000001")
        self.dep = start_deposit(self.acc, "2000")
        self.url = reverse("cashier:paystack_webhook")

    def send(self, payload, secret=KEY):
        body = json.dumps(payload).encode()
        sig = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
        return Client().post(self.url, data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)

    def event(self, kobo=200000):
        return {"event": "charge.success", "data": {"id": 777, "reference": self.dep.reference, "amount": kobo}}

    def bal(self):
        self.acc.refresh_from_db()
        return self.acc.balance

    def test_a_signed_success_credits_once(self):
        self.assertEqual(self.send(self.event()).status_code, 200)
        self.send(self.event())
        self.assertEqual(self.bal(), Decimal("2000"))

    def test_a_different_amount_is_held_for_review(self):
        self.send(self.event(kobo=100000))
        self.dep.refresh_from_db()
        self.assertEqual((self.dep.status, self.bal()), ("review", Decimal("0")))

    def test_a_wrong_signature_does_nothing(self):
        self.assertEqual(self.send(self.event(), secret="wrong").status_code, 401)
        self.assertEqual(self.bal(), Decimal("0"))

    @override_settings(PAYSTACK_SECRET_KEY="")
    def test_with_no_key_every_message_is_rejected(self):
        self.assertEqual(self.send(self.event(), secret="").status_code, 401)

    def test_other_events_are_ignored(self):
        self.assertEqual(self.send({"event": "transfer.success", "data": {}}).status_code, 200)
        self.assertEqual(self.bal(), Decimal("0"))


class DepositHubTests(TestCase):
    def setUp(self):
        u = User.objects.create_user("ann", password="pass12345")
        Account.objects.create(user=u, account_number="1000000001")
        self.client.login(username="ann", password="pass12345")

    @override_settings(DEBUG=False, CASHIER_PROVIDER="cashier.providers.mock.MockCashProvider")
    def test_the_card_option_is_hidden_while_only_the_test_provider_exists(self):
        r = self.client.get(reverse("treasury:deposit"))
        self.assertFalse(r.context["card_on"])
        self.assertContains(r, "1000000001")

@override_settings(DEBUG=False,
                   CASHIER_PROVIDER="cashier.providers.paystack.PaystackProvider",
                   PAYSTACK_SECRET_KEY=KEY)
def test_the_card_option_appears_with_a_real_provider(self):
        self.assertTrue(self.client.get(reverse("treasury:deposit")).context["card_on"])