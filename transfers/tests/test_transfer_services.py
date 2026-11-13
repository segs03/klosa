from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Account
from transactions.models import Transaction
from transfers.models import MoneyRequest, Transfer
from transfers.services import TransferError, internal_transfer, pay_money_request
from transfers.services import send_money


User = get_user_model()



class SendMoneyPinTests(TestCase):
    def setUp(self):
        self.ua, self.a = make("ann", "1000000001", "10000")
        self.ub, self.b = make("ben", "1000000002")
        self.a.set_pin("4827")

    def fresh(self, acc):
        acc.refresh_from_db()
        return acc

    def test_correct_pin_moves_money(self):
        t = send_money(self.a, self.b, "1000", "REF1", "4827")
        self.assertEqual(self.fresh(self.a).balance, Decimal("9000"))
        self.assertEqual(self.fresh(self.b).balance, Decimal("1000"))
        self.assertEqual(Transaction.objects.filter(reference=t.reference).count(), 2)

    def test_wrong_pin_moves_nothing_but_is_counted(self):
        with self.assertRaises(TransferError):
            send_money(self.a, self.b, "1000", "REF2", "0000")
        a = self.fresh(self.a)
        self.assertEqual(a.balance, Decimal("10000"))
        self.assertEqual(a.pin_attempts, 1)                 # the failed try was saved

    def test_locks_after_three_wrong_pins(self):
        for i in range(3):
            with self.assertRaises(TransferError):
                send_money(self.a, self.b, "10", f"R{i}", "0000")
        self.assertTrue(self.fresh(self.a).is_locked())
        with self.assertRaises(TransferError):
            send_money(self.a, self.b, "10", "RX", "4827")    # right PIN, still locked
        self.assertEqual(self.fresh(self.a).balance, Decimal("10000"))

    def test_same_reference_twice_pays_once(self):
        send_money(self.a, self.b, "1000", "SAME", "4827")
        t = send_money(self.a, self.b, "1000", "SAME", "4827")
        self.assertEqual(t.reference, "SAME")
        self.assertEqual(self.fresh(self.a).balance, Decimal("9000"))
        self.assertEqual(Transfer.objects.filter(reference="SAME").count(), 1)

    def test_reused_reference_with_other_details_is_refused(self):
        send_money(self.a, self.b, "1000", "SAME", "4827")
        with self.assertRaises(TransferError):
            send_money(self.a, self.b, "2000", "SAME", "4827")

    def test_missing_pin_or_no_pin_set(self):
        with self.assertRaises(TransferError):
            send_money(self.a, self.b, "10", "R1", "")
        Account.objects.filter(pk=self.a.pk).update(pin_hash="")
        with self.assertRaises(TransferError):
            send_money(self.fresh(self.a), self.b, "10", "R2", "4827")

    def test_closed_receiver_is_refused_even_with_the_right_pin(self):
        Account.objects.filter(pk=self.b.pk).update(closed_at=timezone.now())
        with self.assertRaises(TransferError):
            send_money(self.a, self.fresh(self.b), "10", "R3", "4827")
        self.assertEqual(self.fresh(self.a).balance, Decimal("10000"))

    def test_different_currencies_are_refused(self):
        Account.objects.filter(pk=self.b.pk).update(currency="USD")
        with self.assertRaises(TransferError):
            send_money(self.a, self.fresh(self.b), "10", "R4", "4827")



def make(name, number, balance="0"):
    u = User.objects.create_user(name, password="pass12345", first_name=name.title())
    return u, Account.objects.create(user=u, account_number=number, balance=Decimal(balance))


class InternalTransferTests(TestCase):
    def setUp(self):
        self.ua, self.a = make("ann", "1000000001", "100000")
        self.ub, self.b = make("ben", "1000000002")

    def bal(self, acc):
        acc.refresh_from_db()
        return acc.balance

    def test_moves_money_and_records_everything(self):
        t = internal_transfer(self.a.pk, self.b.pk, "5000.50", note="rent")
        self.assertEqual(self.bal(self.a), Decimal("94999.50"))
        self.assertEqual(self.bal(self.b), Decimal("5000.50"))
        self.assertEqual(t.status, "completed")
        txs = Transaction.objects.filter(reference=t.reference)
        self.assertEqual(txs.count(), 2)
        self.assertEqual(txs.get(account=self.a).transaction_type, "transfer")
        self.assertEqual(txs.get(account=self.b).transaction_type, "deposit")

    def test_bad_amounts_are_refused(self):
        for amt in ["0", "-5", "abc", "NaN", "Infinity", "1.999", ""]:
            with self.assertRaises(TransferError):
                internal_transfer(self.a.pk, self.b.pk, amt)
        self.assertEqual(self.bal(self.a), Decimal("100000"))

    def test_insufficient_balance(self):
        with self.assertRaises(TransferError):
            internal_transfer(self.b.pk, self.a.pk, "10")

    def test_not_to_yourself(self):
        with self.assertRaises(TransferError):
            internal_transfer(self.a.pk, self.a.pk, "10")

    def test_closed_receiver_or_sender_is_refused(self):
        Account.objects.filter(pk=self.b.pk).update(closed_at=timezone.now())
        with self.assertRaises(TransferError):
            internal_transfer(self.a.pk, self.b.pk, "10")
        Account.objects.filter(pk=self.b.pk).update(closed_at=None)
        Account.objects.filter(pk=self.a.pk).update(closed_at=timezone.now())
        with self.assertRaises(TransferError):
            internal_transfer(self.a.pk, self.b.pk, "10")

    def test_tier_zero_single_limit(self):
        with self.assertRaises(TransferError):
            internal_transfer(self.a.pk, self.b.pk, "20001")

    def test_daily_limit(self):
        internal_transfer(self.a.pk, self.b.pk, "20000")
        internal_transfer(self.a.pk, self.b.pk, "20000")
        with self.assertRaises(TransferError):
            internal_transfer(self.a.pk, self.b.pk, "20000")      # 60000 is over the 50000 daily limit
        self.assertEqual(self.bal(self.a), Decimal("60000"))

    def test_everything_is_undone_if_a_write_fails(self):
        with mock.patch("transfers.services.Transaction.objects.create", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                internal_transfer(self.a.pk, self.b.pk, "100")
        self.assertEqual(self.bal(self.a), Decimal("100000"))
        self.assertEqual(self.bal(self.b), Decimal("0"))
        self.assertEqual(Transfer.objects.count(), 0)


class MoneyRequestTests(TestCase):
    def setUp(self):
        self.ua, self.a = make("ann", "1000000001", "50000")     # will pay
        self.ub, self.b = make("ben", "1000000002")              # asks for money
        self.req = MoneyRequest.objects.create(
            requester=self.ub, recipient=self.ua, amount=Decimal("1000"), note="lunch",
            expires_at=timezone.now() + timedelta(days=7))

    def bal(self, acc):
        acc.refresh_from_db()
        return acc.balance

    def test_paying_moves_money_and_links_the_transfer(self):
        t = pay_money_request(self.req.pk, self.ua)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, "paid")
        self.assertEqual(self.req.transfer, t)
        self.assertEqual(self.bal(self.a), Decimal("49000"))
        self.assertEqual(self.bal(self.b), Decimal("1000"))

    def test_cannot_be_paid_twice(self):
        pay_money_request(self.req.pk, self.ua)
        with self.assertRaises(TransferError):
            pay_money_request(self.req.pk, self.ua)
        self.assertEqual(self.bal(self.a), Decimal("49000"))

    def test_only_the_recipient_can_pay(self):
        for user in (self.ub, make("cat", "1000000003")[0]):
            with self.assertRaises(TransferError):
                pay_money_request(self.req.pk, user)
        self.assertEqual(self.bal(self.a), Decimal("50000"))

    def test_expired_request_cannot_be_paid(self):
        MoneyRequest.objects.filter(pk=self.req.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        with self.assertRaises(TransferError):
            pay_money_request(self.req.pk, self.ua)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, "expired")           # kept, because it is saved before the error
        self.assertEqual(self.bal(self.a), Decimal("50000"))

    
def test_declined_or_cancelled_cannot_be_paid(self):
    for status in ("declined", "cancelled"):
        MoneyRequest.objects.filter(pk=self.req.pk).update(
            status=status
        )

        with self.assertRaises(TransferError):
            pay_money_request(self.req.pk, self.ua)

    self.assertEqual(self.bal(self.a), Decimal("50000"))


    def test_failed_payment_leaves_the_request_open(self):
        Account.objects.filter(pk=self.a.pk).update(balance=Decimal("10"))
        with self.assertRaises(TransferError):
            pay_money_request(self.req.pk, self.ua)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, "pending")
        self.assertIsNone(self.req.transfer)

    def test_cannot_request_money_from_yourself(self):
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            MoneyRequest.objects.create(requester=self.ua, recipient=self.ua, amount=Decimal("5"))