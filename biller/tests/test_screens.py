from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import Account
from biller.models import BillPayment
from biller.providers.base import Result
from biller.providers.mock import MockProvider

User = get_user_model()


@override_settings(BILLPAY_PROVIDER="biller.providers.mock.MockProvider")
class ScreenTests(TestCase):
    def setUp(self):
        cache.clear()
        MockProvider.calls = 0
        self.user = User.objects.create_user("ann", password="pass12345", phone_number="+2348012345678")
        self.acc = Account.objects.create(user=self.user, account_number="1000000001", balance=Decimal("100000"))
        self.acc.set_pin("4827")
        self.client.login(username="ann", password="pass12345")

    def url(self, cat="airtime"):
        return reverse("biller:service", args=[cat])

    def token(self, cat="airtime"):
        self.client.get(self.url(cat))
        return self.client.session["bp_token"]

    def data(self, **kw):
        d = {"provider": "mtn", "customer_ref": "08010001234", "amount": "1000"}
        d.update(kw)
        return d

    def review(self, cat="airtime", **kw):
        return self.client.post(self.url(cat), dict(self.data(**kw), step="review"))

    def pay(self, cat="airtime", pin="4827", token=None, **kw):
        token = token or self.token(cat)
        return self.client.post(self.url(cat), dict(self.data(**kw), step="pay", pin=pin, token=token))

    def bal(self):
        self.acc.refresh_from_db()
        return self.acc.balance

    # ---- access ----
    def test_pages_need_login(self):
        for u in (reverse("biller:hub"), reverse("biller:history"), self.url()):
            self.assertEqual(Client().get(u).status_code, 302)

    def test_hub_lists_services_and_locks_betting(self):
        r = self.client.get(reverse("biller:hub"))
        self.assertContains(r, "Airtime")
        self.assertContains(r, "Verify ID")

    def test_every_service_page_loads(self):
        Account.objects.filter(pk=self.acc.pk).update(kyc_tier=2)
        for cat in ("airtime", "data", "education", "electricity", "tv", "internet", "betting"):
            self.assertEqual(self.client.get(self.url(cat)).status_code, 200, cat)
        self.assertEqual(self.client.get(self.url("nonsense")).status_code, 404)

    # ---- review and pay ----
    def test_review_charges_nothing_and_shows_the_name(self):
        r = self.review("electricity", provider="ikedc", customer_ref="12345678901", amount="2000")
        self.assertContains(r, "Test Customer")
        self.assertContains(r, "Confirm and pay")
        self.assertEqual(self.bal(), Decimal("100000"))
        self.assertEqual(BillPayment.objects.count(), 0)

    def test_pay_completes_and_shows_a_receipt(self):
        r = self.pay()
        o = BillPayment.objects.get()
        self.assertRedirects(r, reverse("biller:order", args=[o.reference]))
        self.assertEqual(o.status, "completed")
        self.assertEqual(self.bal(), Decimal("99000"))
        self.assertContains(self.client.get(reverse("biller:order", args=[o.reference])), "Payment successful")

    def test_wrong_pin_charges_nothing_and_is_counted(self):
        r = self.pay(pin="0000")
        self.assertContains(r, "Incorrect")
        self.assertEqual(BillPayment.objects.count(), 0)
        self.assertEqual(self.bal(), Decimal("100000"))
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.pin_attempts, 1)

    def test_data_price_comes_from_the_provider(self):
        self.pay("data", product="plan-1gb", amount="1")
        self.assertEqual(self.bal(), Decimal("99500"))

    def test_bad_inputs_create_nothing(self):
        cases = [("airtime", {"customer_ref": "123"}), ("airtime", {"amount": "0"}),
                 ("airtime", {"amount": "-5"}), ("airtime", {"amount": "abc"}),
                 ("airtime", {"provider": "fake"}), ("data", {}), ("data", {"product": "made-up"})]
        for cat, kw in cases:
            self.pay(cat, **kw)
        self.assertEqual(BillPayment.objects.count(), 0)
        self.assertEqual(self.bal(), Decimal("100000"))

    def test_provider_failure_refunds(self):
        self.pay(customer_ref="08010000000")
        o = BillPayment.objects.get()
        self.assertEqual(o.status, "reversed")
        self.assertEqual(self.bal(), Decimal("100000"))
        self.assertContains(self.client.get(reverse("biller:order", args=[o.reference])), "refunded")

    def test_pending_then_checked_to_completed(self):
        self.pay(customer_ref="08010009999")
        o = BillPayment.objects.get()
        self.assertEqual(o.status, "processing")
        self.assertContains(self.client.get(reverse("biller:order", args=[o.reference])), "Processing")
        self.client.post(reverse("biller:check", args=[o.reference]))
        o.refresh_from_db()
        self.assertEqual(o.status, "completed")

    def test_status_check_is_rate_limited(self):
        self.pay(customer_ref="08010009999")
        o = BillPayment.objects.get()
        with mock.patch.object(MockProvider, "requery", return_value=Result("pending")) as rq:
            for _ in range(3):
                self.client.post(reverse("biller:check", args=[o.reference]))
        self.assertEqual(rq.call_count, 1)

    # ---- safety ----
    def test_a_token_works_once(self):
        token = self.token()
        self.pay(token=token)
        self.pay(token=token)
        self.assertEqual(BillPayment.objects.count(), 1)
        self.assertEqual(MockProvider.calls, 1)
        self.assertEqual(self.bal(), Decimal("99000"))

    def test_wrong_token_is_refused(self):
        self.client.get(self.url())
        self.pay(token="not-the-token")
        self.assertEqual(BillPayment.objects.count(), 0)

    def test_lookup_failure_blocks_the_review(self):
        with mock.patch.object(MockProvider, "verify_customer", return_value=None):
            r = self.review("electricity", provider="ikedc", customer_ref="12345678901", amount="2000")
        self.assertNotContains(r, "Confirm and pay")
        self.assertContains(r, "couldn")

    def test_someone_elses_order_is_a_404_and_the_token_stays_private(self):
        self.pay()
        o = BillPayment.objects.get()
        BillPayment.objects.filter(pk=o.pk).update(token="SECRET-1234")
        self.assertContains(self.client.get(reverse("biller:order", args=[o.reference])), "SECRET-1234")
        other = User.objects.create_user("bob", password="pass12345")
        Account.objects.create(user=other, account_number="1000000002")
        c = Client()
        c.login(username="bob", password="pass12345")
        self.assertEqual(c.get(reverse("biller:order", args=[o.reference])).status_code, 404)
        self.assertEqual(c.post(reverse("biller:check", args=[o.reference])).status_code, 404)

    def test_history_shows_only_my_payments(self):
        self.pay()
        other = User.objects.create_user("bob", password="pass12345")
        b = Account.objects.create(user=other, account_number="1000000002")
        BillPayment.objects.create(account=b, category="airtime", provider_code="glo",
                                   customer_ref="08099998888", amount=Decimal("500"), reference="BPOTHER0001")
        r = self.client.get(reverse("biller:history"))
        self.assertContains(r, "1234")
        self.assertNotContains(r, "8888")

    def test_betting_needs_a_verified_account(self):
    # Unverified account cannot access betting.
        r = self.client.get(self.url("betting"))
        self.assertRedirects(r, reverse("biller:hub"))

    # No payment should be created while unverified.
        self.assertEqual(BillPayment.objects.count(), 0)

    # Upgrade account verification.
        Account.objects.filter(pk=self.acc.pk).update(kyc_tier=1)

    # Verified account can access and pay for betting.
        r = self.client.get(self.url("betting"))
        self.assertEqual(r.status_code, 200)

        self.pay(
        "betting",
        provider="betco",
        customer_ref="USER12345",
        amount="1000",
    )

        self.assertEqual(BillPayment.objects.count(), 1)
        self.assertEqual(BillPayment.objects.get().status, "completed")

    def test_closed_account_cannot_use_the_screens(self):
        from django.utils import timezone
        Account.objects.filter(pk=self.acc.pk).update(closed_at=timezone.now())
        self.assertEqual(self.client.get(self.url()).status_code, 302)