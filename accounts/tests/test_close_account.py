from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Account, CreditAccount, Passkey

User = get_user_model()


def make_user(name, number, balance="0", pin="4827"):
    user = User.objects.create_user(name, password="pass12345")
    acc = Account.objects.create(user=user, account_number=number, balance=Decimal(balance))
    acc.set_pin(pin)
    return user, acc


class CloseAccountTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user, self.acc = make_user("closer", "1000000021")
        User.objects.filter(pk=self.user.pk).update(phone_number="+2348012345678", email="c@x.com")
        self.client.login(username="closer", password="pass12345")
        self.url = reverse("accounts:close_account")
        self.ok = {"password": "pass12345", "pin": "4827", "confirm": "close"}

    def closed(self):
        self.acc.refresh_from_db()
        return self.acc.closed_at is not None

    def test_closes_with_zero_balance(self):
        r = self.client.post(self.url, self.ok)
        self.assertRedirects(r, reverse("accounts:login"), fetch_redirect_response=False)
        self.assertTrue(self.closed())
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertIsNone(self.user.phone_number)
        self.assertFalse(Client().login(username="closer", password="pass12345"))

    def test_money_in_account_blocks_closing(self):
        Account.objects.filter(pk=self.acc.pk).update(balance=Decimal("500"))
        self.client.post(self.url, self.ok)
        self.assertFalse(self.closed())

    def test_credit_owed_blocks_closing(self):
        CreditAccount.objects.create(account=self.acc, credit_limit=Decimal("1000"),
                                     outstanding_balance=Decimal("100"), status="active")
        self.client.post(self.url, self.ok)
        self.assertFalse(self.closed())

    def test_wrong_password_pin_or_confirm_word(self):
        for bad in ({"password": "nope"}, {"pin": "0000"}, {"confirm": "yes"}, {"pin": ""}):
            self.client.post(self.url, {**self.ok, **bad})
        self.assertFalse(self.closed())

    def test_history_is_kept_and_passkeys_removed(self):
        from transactions.models import Transaction
        Transaction.objects.create(account=self.acc, transaction_type="deposit", amount=Decimal("10"))
        Passkey.objects.create(user=self.user, credential_id="abc", public_key=b"x")
        self.client.post(self.url, self.ok)
        self.assertEqual(Transaction.objects.filter(account=self.acc).count(), 1)
        self.assertEqual(Passkey.objects.filter(user=self.user).count(), 0)

    def test_phone_can_be_used_again(self):
        self.client.post(self.url, self.ok)
        self.assertFalse(User.objects.filter(phone_number="+2348012345678").exists())

    def test_anonymous_goes_to_login(self):
        self.assertEqual(Client().get(self.url).status_code, 302)

    def test_closing_is_rate_limited(self):
        for _ in range(5):
            self.client.post(self.url, {**self.ok, "pin": "0000"})
        self.assertEqual(self.client.post(self.url, self.ok).status_code, 429)
        self.assertFalse(self.closed())