from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Account
from biller.models import BillPayment

User = get_user_model()


@override_settings(BILLPAY_PROVIDER="biller.providers.mock.MockProvider")
class StepFieldTests(TestCase):
    def setUp(self):
        cache.clear()
        u = User.objects.create_user("ann", password="pass12345")
        a = Account.objects.create(user=u, account_number="1000000001", balance=Decimal("100000"))
        a.set_pin("4827")
        self.client.login(username="ann", password="pass12345")
        self.url = reverse("biller:service", args=["airtime"])

    def test_payment_goes_through_even_if_the_browser_drops_the_step_field(self):
        self.client.get(self.url)
        r = self.client.post(self.url, {"provider": "mtn", "customer_ref": "08010001234", "amount": "1000",
                                        "pin": "4827", "token": self.client.session["bp_token"]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(BillPayment.objects.get().status, "completed")

    def test_the_review_step_alone_charges_nothing(self):
        self.client.get(self.url)
        self.client.post(self.url, {"provider": "mtn", "customer_ref": "08010001234", "amount": "1000"})
        self.assertEqual(BillPayment.objects.count(), 0)