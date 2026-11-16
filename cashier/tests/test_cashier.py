import hashlib
import hmac
import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Account
from cashier import rules
from cashier.models import Deposit, Withdrawal
from cashier.providers.mock import MockCashProvider
from cashier.services import CashierError, credit_deposit, start_deposit, start_withdrawal
from transactions.models import Transaction

User = get_user_model()
SECRET = "s3cret"
_n = iter(range(1, 10**6))


def sign(body, secret=SECRET):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def hook(client, payload, secret=SECRET):
    body = json.dumps(payload).encode()
    return client.post(reverse("cashier:webhook"), data=body, content_type="application/json",
                       HTTP_X_SIGNATURE=sign(body, secret))


@override_settings(CASHIER_PROVIDER="cashier.providers.mock.MockCashProvider", CASHIER_WEBHOOK_SECRET=SECRET,
                   CASHIER_ALLOW_MOCK_PAYPAGE=True, CASHIER_WITHDRAW_MIN_TIER=1, KLOSA_BANK_CODE="999")
class Base(TestCase):
    def setUp(self):
        cache.clear()
        MockCashProvider.calls, MockCashProvider.paid = 0, {}
        self.user = User.objects.create_user("ann", password="pass12345")
        self.acc = Account.objects.create(user=self.user, account_number="1000000001",
                                          balance=Decimal("100000"), kyc_tier=1)
        self.acc.set_pin("4827")

    def bal(self):
        self.acc.refresh_from_db()
        return self.acc.balance


class DepositTests(Base):
    def dep(self, amount="1000"):
        return start_deposit(Account.objects.get(pk=self.acc.pk), amount)

    def pay(self, dep, amount=None, **kw):
        return hook(Client(), {"event": "deposit.success", "reference": dep.reference,
                               "amount": amount or str(dep.amount), "provider_reference": "P" + dep.reference, **kw})

    def test_starting_a_deposit_creates_a_pending_row_with_a_checkout_url(self):
        d = self.dep()
        self.assertEqual((d.status, d.method), ("pending", "card"))
        self.assertTrue(d.checkout_url)
        self.assertEqual(self.bal(), Decimal("100000"))

    def test_bad_amounts_are_refused(self):
        for amt in ["0", "-5", "abc", "NaN", "Infinity", "1e999", "10.999", "99", "1000001", ""]:
            with self.assertRaises(CashierError, msg=amt):
                self.dep(amt)
        self.assertEqual(Deposit.objects.count(), 0)

    def test_a_signed_webhook_credits_once_even_if_sent_twice(self):
        d = self.dep()
        self.assertEqual(self.pay(d).status_code, 200)
        self.pay(d)
        self.assertEqual(self.bal(), Decimal("101000"))
        self.assertEqual(Transaction.objects.filter(reference=d.reference).count(), 1)
        d.refresh_from_db()
        self.assertEqual(d.status, "completed")

    def test_unsigned_or_wrongly_signed_webhooks_do_nothing(self):
        d = self.dep()
        payload = json.dumps({"event": "deposit.success", "reference": d.reference, "amount": "1000.00"}).encode()
        for headers in ({}, {"HTTP_X_SIGNATURE": "deadbeef"}, {"HTTP_X_SIGNATURE": sign(payload, "wrong")}):
            r = Client().post(reverse("cashier:webhook"), data=payload, content_type="application/json", **headers)
            self.assertEqual(r.status_code, 401)
        self.assertEqual(self.bal(), Decimal("100000"))

    @override_settings(CASHIER_WEBHOOK_SECRET="")
    def test_with_no_secret_every_webhook_is_rejected(self):
        d = self.dep()
        self.assertEqual(self.pay(d).status_code, 401)
        self.assertEqual(self.bal(), Decimal("100000"))

    def test_a_different_amount_goes_to_review_and_is_not_credited(self):
        d = self.dep("1000")
        self.pay(d, amount="500")
        d.refresh_from_db()
        self.assertEqual(d.status, "review")
        self.assertEqual(self.bal(), Decimal("100000"))
        self.pay(d, amount="1000")                           # a later correct message cannot override a review
        self.assertEqual(self.bal(), Decimal("100000"))

    def test_a_closed_account_is_not_credited(self):
        d = self.dep()
        Account.objects.filter(pk=self.acc.pk).update(closed_at=timezone.now())
        self.pay(d)
        d.refresh_from_db()
        self.assertEqual(d.status, "review")
        self.assertEqual(self.bal(), Decimal("100000"))

    def test_malformed_and_unknown_messages(self):
        c = Client()
        body = b"not json"
        self.assertEqual(c.post(reverse("cashier:webhook"), data=body, content_type="application/json",
                                HTTP_X_SIGNATURE=sign(body)).status_code, 400)
        self.assertEqual(hook(c, {"event": "deposit.success", "reference": "NOPE", "amount": "5"}).status_code, 200)
        self.assertEqual(hook(c, {"nothing": 1}).status_code, 400)

    def test_inbound_bank_transfer_is_credited_once(self):
        p = {"event": "transfer.received", "account_number": "1000000001", "amount": "2500.00",
             "provider_reference": "NIP-1", "sender": "Ben"}
        hook(Client(), p)
        hook(Client(), p)
        self.assertEqual(self.bal(), Decimal("102500"))
        self.assertEqual(Deposit.objects.filter(method="transfer").count(), 1)

    def test_inbound_transfer_to_a_closed_account_needs_review(self):
        Account.objects.filter(pk=self.acc.pk).update(closed_at=timezone.now())
        hook(Client(), {"event": "transfer.received", "account_number": "1000000001", "amount": "100",
                        "provider_reference": "NIP-2"})
        self.assertEqual(Deposit.objects.get().status, "review")
        self.assertEqual(self.bal(), Decimal("100000"))

    def test_reconcile_credits_a_payment_the_webhook_missed(self):
        d = self.dep()
        MockCashProvider.paid[d.reference] = d.amount
        Deposit.objects.filter(pk=d.pk).update(created_at=timezone.now() - timezone.timedelta(minutes=30))
        call_command("reconcile_cashier")
        call_command("reconcile_cashier")
        self.assertEqual(self.bal(), Decimal("101000"))

    def test_fake_payment_page_pays_and_is_closed_when_the_flag_is_off(self):
        c = Client()
        c.login(username="ann", password="pass12345")
        d = self.dep()
        c.post(reverse("cashier:mockpay", args=[d.reference]), {"action": "pay"})
        self.assertEqual(self.bal(), Decimal("101000"))
        d2 = self.dep()
        with override_settings(CASHIER_ALLOW_MOCK_PAYPAGE=False):
            self.assertEqual(c.get(reverse("cashier:mockpay", args=[d2.reference])).status_code, 404)

    def test_pages_are_private_and_post_only(self):
        d = self.dep()
        other = User.objects.create_user("bob", password="pass12345")
        Account.objects.create(user=other, account_number="1000000002")
        c = Client()
        c.login(username="bob", password="pass12345")
        for name in ("cashier:deposit_status", "cashier:deposit_poll", "cashier:mockpay"):
            self.assertEqual(c.post(reverse(name, args=[d.reference])).status_code, 404)
        self.client.login(username="ann", password="pass12345")
        self.assertEqual(self.client.get(reverse("cashier:deposit_poll", args=[d.reference])).status_code, 405)

    def test_deposit_page_sends_you_to_the_checkout(self):
        self.client.login(username="ann", password="pass12345")
        r = self.client.post(reverse("cashier:deposit"), {"amount": "2000"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Deposit.objects.get().amount, Decimal("2000"))


class WithdrawalTests(Base):
    def w(self, **kw):
        args = dict(bank_name="GTBank", account_number="0123456789", account_name="Test Account Holder",
                    amount="1000", reference=f"WDT{next(_n):07d}", pin="4827")
        args.update(kw)
        return start_withdrawal(Account.objects.get(pk=self.acc.pk), **args)

    def test_success_takes_the_money_and_records_it(self):
        o = self.w()
        self.assertEqual(o.status, "completed")
        self.assertEqual(self.bal(), Decimal("99000"))
        self.assertTrue(Transaction.objects.filter(reference=o.reference, transaction_type="withdrawal").exists())

    def test_the_fee_is_taken_with_the_amount(self):
        with mock.patch.object(rules, "WITHDRAW_FEE", Decimal("50")):
            self.w()
        self.assertEqual(self.bal(), Decimal("98950"))

    def test_a_declined_payout_is_refunded_once(self):
        o = self.w(account_number="0123450000")
        self.assertEqual(o.status, "reversed")
        self.assertEqual(self.bal(), Decimal("100000"))
        call_command("reconcile_cashier", min_age=0)
        self.assertEqual(Transaction.objects.filter(reference=o.reference + "R").count(), 1)

    def test_pending_stays_held_then_completes_or_refunds(self):
        ok = self.w(account_number="0123459999")
        bad = self.w(account_number="0123458888")
        self.assertEqual((ok.status, bad.status), ("processing", "processing"))
        self.assertEqual(self.bal(), Decimal("98000"))
        call_command("reconcile_cashier", min_age=0)
        ok.refresh_from_db()
        bad.refresh_from_db()
        self.assertEqual((ok.status, bad.status), ("completed", "reversed"))
        self.assertEqual(self.bal(), Decimal("99000"))

    def test_a_provider_error_never_refunds_on_a_guess(self):
        with mock.patch.object(MockCashProvider, "payout", side_effect=RuntimeError("timeout")):
            o = self.w()
        self.assertEqual(o.status, "processing")
        self.assertEqual(self.bal(), Decimal("99000"))

    def test_the_same_reference_pays_once(self):
        r = "WDSAME0001"
        a = self.w(reference=r)
        b = self.w(reference=r)
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(self.bal(), Decimal("99000"))
        self.assertEqual(MockCashProvider.calls, 1)
        with self.assertRaises(CashierError):
            self.w(reference=r, amount="2000")

    def test_wrong_pin_takes_nothing_and_three_lock_the_account(self):
        for _ in range(3):
            with self.assertRaises(CashierError):
                self.w(pin="0000")
        with self.assertRaises(CashierError):
            self.w(pin="4827")
        self.assertEqual(self.bal(), Decimal("100000"))
        self.assertEqual(Withdrawal.objects.count(), 0)

    def test_tier_limits_balance_and_closed_account(self):
        Account.objects.filter(pk=self.acc.pk).update(kyc_tier=0)
        with self.assertRaises(CashierError):
            self.w()
        Account.objects.filter(pk=self.acc.pk).update(kyc_tier=1)
        with self.assertRaises(CashierError):
            self.w(amount="50001")                           # over the tier-1 single limit
        Account.objects.filter(pk=self.acc.pk).update(balance=Decimal("500"))
        with self.assertRaises(CashierError):
            self.w()
        Account.objects.filter(pk=self.acc.pk).update(balance=Decimal("100000"), closed_at=timezone.now())
        with self.assertRaises(CashierError):
            self.w()
        self.assertEqual(Withdrawal.objects.count(), 0)

    def test_the_daily_limit(self):
        Account.objects.filter(pk=self.acc.pk).update(balance=Decimal("500000"))
        for _ in range(4):
            self.w(amount="50000")
        with self.assertRaises(CashierError):
            self.w(amount="1000")                            # 200,000 is the tier-1 daily limit

    def test_bad_inputs_are_refused(self):
        for kw in ({"bank_name": "Klosa Bank"}, {"bank_name": "Made Up Bank"}, {"account_number": "123"},
                   {"account_number": "12345abcde"}, {"amount": "0"}, {"amount": "-5"}, {"amount": "NaN"},
                   {"amount": "99"}, {"account_name": ""}, {"pin": ""}):
            with self.assertRaises(CashierError, msg=str(kw)):
                self.w(**kw)
        self.assertEqual(self.bal(), Decimal("100000"))


class WithdrawViewTests(Base):
    def setUp(self):
        super().setUp()
        self.client.login(username="ann", password="pass12345")
        self.url = reverse("cashier:withdraw")
        self.form = {"bank_name": "GTBank", "account_number": "0123456789", "amount": "1000"}

    def review(self, **kw):
        return self.client.post(self.url, {**self.form, **kw, "step": "review"})

    def pay(self, **kw):
        self.client.get(self.url)
        data = {**self.form, **kw, "pin": "4827", "token": self.client.session["wd_token"]}
        return self.client.post(self.url, data)

    def test_a_tier_zero_user_is_sent_away(self):
        Account.objects.filter(pk=self.acc.pk).update(kyc_tier=0)
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_review_shows_the_bank_name_and_takes_nothing(self):
        r = self.review()
        self.assertContains(r, "Test Account Holder")
        self.assertContains(r, "Confirm and withdraw")
        self.assertEqual(self.bal(), Decimal("100000"))

    def test_an_unknown_account_blocks_the_review(self):
        r = self.review(account_number="0123451111")
        self.assertNotContains(r, "Confirm and withdraw")

    def test_payment_works_even_if_the_browser_drops_the_step_field(self):
        self.client.get(self.url)
        self.review()
        r = self.pay()
        o = Withdrawal.objects.get()
        self.assertRedirects(r, reverse("cashier:withdraw_order", args=[o.reference]))
        self.assertEqual(self.bal(), Decimal("99000"))
        self.assertContains(self.client.get(reverse("cashier:withdraw_order", args=[o.reference])), "Withdrawal sent")

    def test_paying_to_an_account_that_was_not_reviewed_is_refused(self):
        self.review()
        self.pay(account_number="0123459876")
        self.assertEqual(Withdrawal.objects.count(), 0)

    def test_a_token_works_once(self):
        self.review()
        self.client.get(self.url)
        token = self.client.session["wd_token"]
        data = {**self.form, "pin": "4827", "token": token}
        self.client.post(self.url, data)
        self.client.post(self.url, data)
        self.assertEqual(Withdrawal.objects.count(), 1)
        self.assertEqual(self.bal(), Decimal("99000"))

    def test_order_pages_are_private(self):
        self.review()
        self.pay()
        o = Withdrawal.objects.get()
        other = User.objects.create_user("bob", password="pass12345")
        Account.objects.create(user=other, account_number="1000000002")
        c = Client()
        c.login(username="bob", password="pass12345")
        self.assertEqual(c.get(reverse("cashier:withdraw_order", args=[o.reference])).status_code, 404)
        self.assertEqual(c.post(reverse("cashier:withdraw_poll", args=[o.reference])).status_code, 404)