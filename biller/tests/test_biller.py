from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Account
from biller.models import BillPayment
from biller.providers.mock import MockProvider
from biller.services import PaymentError, start_payment
from transactions.models import Transaction

User = get_user_model()
_n = iter(range(1, 10**6))


@override_settings(BILLER_PROVIDER="biller.providers.mock.MockProvider")
class BillerTests(TestCase):
    def setUp(self):
        MockProvider.calls = 0
        u = User.objects.create_user("ann", password="x")
        self.acc = Account.objects.create(user=u, account_number="1000000001", balance=Decimal("100000"))
        self.acc.set_pin("4827")

    def pay(self, **kw):
        args = dict(category="airtime", provider_code="mtn", customer_ref="08010001234",
                    amount="1000", reference=f"BPTEST{next(_n):06d}", pin="4827")
        args.update(kw)
        return start_payment(Account.objects.get(pk=self.acc.pk), **args)

    def bal(self):
        self.acc.refresh_from_db()
        return self.acc.balance

    def test_success_debits_and_records(self):
        o = self.pay()
        self.assertEqual(o.status, "completed")
        self.assertEqual(self.bal(), Decimal("99000"))
        self.assertTrue(Transaction.objects.filter(reference=o.reference, transaction_type="payment").exists())

    def test_provider_failure_refunds(self):
        o = self.pay(customer_ref="08010000000")
        self.assertEqual(o.status, "reversed")
        self.assertEqual(self.bal(), Decimal("100000"))
        self.assertTrue(Transaction.objects.filter(reference=o.reference + "R").exists())

    def test_pending_keeps_the_money_held(self):
        o = self.pay(customer_ref="08010009999")
        self.assertEqual(o.status, "processing")
        self.assertEqual(self.bal(), Decimal("99000"))

    def test_reconcile_completes_a_pending_order(self):
        o = self.pay(customer_ref="08010009999")
        call_command("reconcile_bills", min_age=0)
        o.refresh_from_db()
        self.assertEqual(o.status, "completed")
        self.assertEqual(self.bal(), Decimal("99000"))

    def test_reconcile_refunds_a_declined_order_once(self):
        o = self.pay(customer_ref="08010008888")
        call_command("reconcile_bills", min_age=0)
        call_command("reconcile_bills", min_age=0)
        o.refresh_from_db()
        self.assertEqual(o.status, "reversed")
        self.assertEqual(self.bal(), Decimal("100000"))
        self.assertEqual(Transaction.objects.filter(reference=o.reference + "R").count(), 1)

    def test_provider_error_never_refunds_on_a_guess(self):
        with mock.patch.object(MockProvider, "purchase", side_effect=RuntimeError("timeout")):
            o = self.pay()
        self.assertEqual(o.status, "processing")
        self.assertEqual(self.bal(), Decimal("99000"))

    def test_same_reference_charges_and_buys_once(self):
        a = self.pay(reference="BPSAME00001")
        b = self.pay(reference="BPSAME00001")
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(self.bal(), Decimal("99000"))
        self.assertEqual(MockProvider.calls, 1)

    def test_reused_reference_with_other_details_is_refused(self):
        self.pay(reference="BPSAME00002")
        with self.assertRaises(PaymentError):
            self.pay(reference="BPSAME00002", amount="2000")

    def test_wrong_pin_charges_nothing_and_is_counted(self):
        with self.assertRaises(PaymentError):
            self.pay(pin="0000")
        self.assertEqual(self.bal(), Decimal("100000"))
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.pin_attempts, 1)

    def test_pin_locks_after_three_wrong_tries(self):
        for _ in range(3):
            with self.assertRaises(PaymentError):
                self.pay(pin="0000")
        with self.assertRaises(PaymentError):
            self.pay(pin="4827")
        self.assertEqual(self.bal(), Decimal("100000"))

    def test_insufficient_balance_and_closed_account(self):
        Account.objects.filter(pk=self.acc.pk).update(balance=Decimal("500"))
        with self.assertRaises(PaymentError):
            self.pay()
        Account.objects.filter(pk=self.acc.pk).update(balance=Decimal("100000"), closed_at=timezone.now())
        with self.assertRaises(PaymentError):
            self.pay()
        self.assertEqual(BillPayment.objects.count(), 0)

    def test_bad_inputs_are_refused(self):
        for kw in ({"customer_ref": "123"}, {"amount": "0"}, {"amount": "-5"}, {"amount": "NaN"},
                   {"amount": "49"}, {"amount": "50001"}, {"category": "nonsense"}, {"pin": ""}):
            with self.assertRaises(PaymentError, msg=str(kw)):
                self.pay(**kw)
        self.assertEqual(self.bal(), Decimal("100000"))

    def test_data_price_comes_from_the_provider_not_the_browser(self):
        o = self.pay(category="data", product_code="plan-1gb", amount="1")
        self.assertEqual(o.amount, Decimal("500"))
        self.assertEqual(self.bal(), Decimal("99500"))

    def test_unknown_product_is_refused(self):
        with self.assertRaises(PaymentError):
            self.pay(category="data", product_code="made-up", amount="500")
        with self.assertRaises(PaymentError):
            self.pay(category="data", amount="500")

    def test_tier_zero_daily_limit(self):
        self.pay(amount="30000")
        with self.assertRaises(PaymentError):
            self.pay(amount="30000")                     # 60,000 is over the 50,000 tier-0 limit

    def test_betting_needs_a_verified_account(self):
        with self.assertRaises(PaymentError):
            self.pay(category="betting", customer_ref="USER12345", amount="1000")
        Account.objects.filter(pk=self.acc.pk).update(kyc_tier=1)
        self.assertEqual(self.pay(category="betting", customer_ref="USER12345", amount="1000").status, "completed")

    def test_betting_has_its_own_daily_cap(self):
        Account.objects.filter(pk=self.acc.pk).update(kyc_tier=2, balance=Decimal("500000"))
        self.pay(category="betting", customer_ref="USER12345", amount="30000")
        with self.assertRaises(PaymentError):
            self.pay(category="betting", customer_ref="USER12345", amount="30000")