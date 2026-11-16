from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Account
from transactions.models import Transaction

User = get_user_model()


class HomeTotalsTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("ann", password="pass12345")
        self.acc = Account.objects.create(user=self.u, account_number="1000000001", balance=Decimal("5000"))
        self.client.login(username="ann", password="pass12345")

    def add(self, kind, amount, desc=""):
        Transaction.objects.create(account=self.acc, transaction_type=kind, amount=Decimal(amount), description=desc)

    def test_totals_follow_the_rules(self):
        self.add("deposit", "1000")
        self.add("deposit", "300", "Refund: Airtime failed")
        self.add("transfer", "200")
        self.add("payment", "100")
        self.add("withdrawal", "50")
        self.add("savings_deposit", "500")
        c = self.client.get(reverse("accounts:home")).context
        self.assertEqual(c["total_income"], Decimal("1000"))        # the refund is not income
        self.assertEqual(c["total_expenses"], Decimal("350"))       # transfers count, savings do not
        self.assertEqual(c["monthly_spending"], Decimal("350"))

    def test_the_list_is_limited_but_the_totals_are_not(self):
        for _ in range(60):
            self.add("transfer", "10")
        c = self.client.get(reverse("accounts:home")).context
        self.assertEqual(len(c["transactions"]), 50)
        self.assertEqual(c["total_expenses"], Decimal("600"))

    def test_a_user_with_no_account_still_gets_a_page(self):
        User.objects.create_user("ghost", password="pass12345")
        self.client.logout()
        self.client.login(username="ghost", password="pass12345")
        self.assertEqual(self.client.get(reverse("accounts:home")).status_code, 200)