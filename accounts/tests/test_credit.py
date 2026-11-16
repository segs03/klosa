from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.credit_services import credit_summary, refund_credit_purchase, repay_credit, spend_on_credit
from accounts.models import Account, CreditAccount
from cards.models import BankCard
from transactions.models import CreditTransaction, Transaction

User = get_user_model()
_n = iter(range(1, 10**6))


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("ann", password="x")
        self.acc = Account.objects.create(user=self.user, account_number="1000000001", balance=Decimal("50000"))
        self.acc.set_pin("4827")
        self.credit = CreditAccount.objects.create(account=self.acc, credit_limit=Decimal("100000"), status="active")
        self.card = BankCard.objects.create(
            account=self.acc, card_type="credit", cardholder_name="ANN", last_four="1234",
            expiry_month=12, expiry_year=timezone.localdate().year + 2)

    def ref(self):
        return f"T{next(_n):09d}"

    def spend(self, amount="1000", ref=None):
        return spend_on_credit(self.card, amount, reference=ref or self.ref(), merchant="Shop")

    def owed(self):
        self.credit.refresh_from_db()
        return self.credit.outstanding_balance

    def bal(self):
        self.acc.refresh_from_db()
        return self.acc.balance


class SpendTests(Base):
    def test_a_purchase_uses_credit_and_never_touches_the_balance(self):
        t = self.spend("1000")
        self.assertEqual((self.owed(), self.bal()), (Decimal("1000"), Decimal("50000")))
        self.assertEqual((t.transaction_type, t.amount), ("purchase", Decimal("1000")))
        self.assertEqual(self.credit.available_credit, Decimal("99000"))

    def test_the_same_reference_spends_once(self):
        r = self.ref()
        self.spend("1000", r)
        self.spend("1000", r)
        self.assertEqual((self.owed(), CreditTransaction.objects.count()), (Decimal("1000"), 1))
        with self.assertRaises(ValidationError):
            self.spend("2000", r)

    def test_the_limit_is_exact(self):
        self.spend("100000.00")
        with self.assertRaises(ValidationError):
            self.spend("0.01")
        self.assertEqual(self.owed(), Decimal("100000"))

    def test_over_the_limit_changes_nothing(self):
        with self.assertRaises(ValidationError):
            self.spend("100000.01")
        self.assertEqual((self.owed(), CreditTransaction.objects.count()), (Decimal("0"), 0))

    def test_bad_amounts(self):
        for amt in ["0", "-5", "abc", "NaN", "Infinity", "10.999", ""]:
            with self.assertRaises(ValidationError, msg=amt):
                self.spend(amt)
        self.assertEqual(self.owed(), Decimal("0"))

    def test_declined_when_the_card_facility_or_account_is_not_usable(self):
        year = timezone.localdate().year

        def restore():
            BankCard.objects.filter(pk=self.card.pk).update(status="active", expiry_year=year + 2, card_type="credit")
            CreditAccount.objects.filter(pk=self.credit.pk).update(status="active")
            Account.objects.filter(pk=self.acc.pk).update(closed_at=None)

        breaks = {
            "blocked": lambda: BankCard.objects.filter(pk=self.card.pk).update(status="blocked"),
            "expired": lambda: BankCard.objects.filter(pk=self.card.pk).update(expiry_year=year - 1),
            "debit": lambda: BankCard.objects.filter(pk=self.card.pk).update(card_type="debit"),
            "suspended": lambda: CreditAccount.objects.filter(pk=self.credit.pk).update(status="suspended"),
            "closed": lambda: Account.objects.filter(pk=self.acc.pk).update(closed_at=timezone.now()),
        }
        for name, apply in breaks.items():
            with self.subTest(name):
                apply()
                with self.assertRaises(ValidationError):
                    self.spend()
                restore()
        self.assertEqual(self.owed(), Decimal("0"))


class RepayTests(Base):
    def test_repaying_moves_money_and_writes_both_ledgers(self):
        self.spend("10000")
        r = self.ref()
        repay_credit(self.credit, "4000", reference=r, pin="4827")
        self.assertEqual((self.owed(), self.bal()), (Decimal("6000"), Decimal("46000")))
        self.assertTrue(Transaction.objects.filter(account=self.acc, transaction_type="payment", reference=r).exists())
        self.assertTrue(CreditTransaction.objects.filter(reference=r, transaction_type="repayment").exists())

    def test_the_same_reference_repays_once(self):
        self.spend("10000")
        r = self.ref()
        repay_credit(self.credit, "4000", reference=r, pin="4827")
        repay_credit(self.credit, "4000", reference=r, pin="4827")
        self.assertEqual(self.bal(), Decimal("46000"))
        with self.assertRaises(ValidationError):
            repay_credit(self.credit, "5000", reference=r, pin="4827")

    def test_more_than_owed_or_more_than_the_balance(self):
        self.spend("10000")
        with self.assertRaises(ValidationError):
            repay_credit(self.credit, "10000.01", pin="4827")
        Account.objects.filter(pk=self.acc.pk).update(balance=Decimal("100"))
        with self.assertRaises(ValidationError):
            repay_credit(self.credit, "500", pin="4827")
        self.assertEqual(self.owed(), Decimal("10000"))

    def test_wrong_pin_changes_nothing_and_is_counted(self):
        self.spend("10000")
        with self.assertRaises(ValidationError):
            repay_credit(self.credit, "4000", reference=self.ref(), pin="0000")
        self.assertEqual((self.owed(), self.bal()), (Decimal("10000"), Decimal("50000")))
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.pin_attempts, 1)

    def test_three_wrong_pins_lock_repayments(self):
        self.spend("10000")
        for _ in range(3):
            with self.assertRaises(ValidationError):
                repay_credit(self.credit, "1000", reference=self.ref(), pin="0000")
        with self.assertRaises(ValidationError):
            repay_credit(self.credit, "1000", reference=self.ref(), pin="4827")
        self.assertEqual(self.owed(), Decimal("10000"))

    def test_a_suspended_customer_can_still_repay(self):
        self.spend("10000")
        CreditAccount.objects.filter(pk=self.credit.pk).update(status="suspended")
        repay_credit(self.credit, "1000", pin="4827")
        self.assertEqual(self.owed(), Decimal("9000"))

    def test_bad_amounts(self):
        self.spend("10000")
        for amt in ["0", "-5", "NaN", "abc", "1.999"]:
            with self.assertRaises(ValidationError, msg=amt):
                repay_credit(self.credit, amt, pin="4827")


class RefundTests(Base):
    def test_a_refund_lowers_what_is_owed_once(self):
        r = self.ref()
        self.spend("1000", r)
        refund_credit_purchase(r)
        refund_credit_purchase(r)
        self.assertEqual(self.owed(), Decimal("0"))
        self.assertEqual(CreditTransaction.objects.filter(transaction_type="refund").count(), 1)

    def test_a_partial_refund_and_one_that_is_too_big(self):
        r = self.ref()
        self.spend("1000", r)
        with self.assertRaises(ValidationError):
            refund_credit_purchase(r, "1000.01")
        refund_credit_purchase(r, "400")
        self.assertEqual(self.owed(), Decimal("600"))

    def test_a_refund_after_repaying_goes_back_to_the_balance(self):
        r = self.ref()
        self.spend("1000", r)
        repay_credit(self.credit, "1000", pin="4827")
        refund_credit_purchase(r)
        self.assertEqual((self.owed(), self.bal()), (Decimal("0"), Decimal("50000")))
        self.assertTrue(Transaction.objects.filter(account=self.acc, description__startswith="Refund:").exists())

    def test_an_unknown_purchase(self):
        with self.assertRaises(ValidationError):
            refund_credit_purchase("NOPE")


class SummaryTests(Base):
    def test_numbers(self):
        self.spend("25000")
        s = credit_summary(self.acc)
        self.assertEqual((s["limit"], s["owed"], s["available"], s["pct"]),
                         (Decimal("100000"), Decimal("25000"), Decimal("75000"), 25))

    def test_none_without_an_active_facility(self):
        CreditAccount.objects.filter(pk=self.credit.pk).update(status="suspended")
        self.assertIsNone(credit_summary(self.acc))