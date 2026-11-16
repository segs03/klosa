from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Account
from biller.models import BillPayment
from transactions.models import Transaction
from transactions.summary import summarize

User = get_user_model()


class SummaryTests(TestCase):
    def setUp(self):
        u = User.objects.create_user("ann", password="x")
        self.acc = Account.objects.create(user=u, account_number="1000000001")

    def tx(self, kind, amount, ref="", desc=""):
        return Transaction.objects.create(account=self.acc, transaction_type=kind,
                                          amount=Decimal(amount), reference=ref, description=desc)

    def test_each_total(self):
        for kind, amt in (("deposit", "1000"), ("transfer", "200"), ("payment", "100"), ("withdrawal", "50")):
            self.tx(kind, amt)
        s = summarize(self.acc)
        self.assertEqual((s["income"], s["expenses"], s["bills"], s["transfers"], s["withdrawals"]),
                         (Decimal("1000"), Decimal("350"), Decimal("100"), Decimal("200"), Decimal("50")))

    def test_savings_moves_are_neither_income_nor_spending(self):
        self.tx("savings_deposit", "500")
        self.tx("savings_withdrawal", "500")
        s = summarize(self.acc)
        self.assertEqual((s["income"], s["expenses"]), (Decimal("0"), Decimal("0")))

    def test_a_refunded_bill_counts_nowhere(self):
        self.tx("payment", "500", "BP1")
        self.tx("deposit", "500", "BP1R", "Refund: Airtime failed")
        BillPayment.objects.create(account=self.acc, category="airtime", provider_code="mtn",
                                   customer_ref="08010001234", amount=Decimal("500"),
                                   reference="BP1", status="reversed")
        s = summarize(self.acc)
        self.assertEqual((s["income"], s["expenses"]), (Decimal("0"), Decimal("0")))

    def test_a_bill_still_processing_counts_as_spent(self):
        self.tx("payment", "500", "BP2")
        BillPayment.objects.create(account=self.acc, category="airtime", provider_code="mtn",
                                   customer_ref="08010001234", amount=Decimal("500"), reference="BP2")
        self.assertEqual(summarize(self.acc)["expenses"], Decimal("500"))

    def test_a_refund_is_never_income(self):
        self.tx("deposit", "300", "X", "Refund: Data failed")
        self.assertEqual(summarize(self.acc)["income"], Decimal("0"))

    def test_since_limits_the_period(self):
        old = self.tx("transfer", "900")
        Transaction.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=40))
        self.tx("transfer", "100")
        self.assertEqual(summarize(self.acc, since=timezone.now() - timedelta(days=5))["expenses"], Decimal("100"))
        self.assertEqual(summarize(self.acc)["expenses"], Decimal("1000"))

    def test_an_empty_account_is_all_zero(self):
        self.assertTrue(all(v == Decimal("0") for v in summarize(self.acc).values()))