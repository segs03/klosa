from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.credit_services import repay_credit
from accounts.models import Account, CreditAccount
from accounts.tests.helpers import make_account, make_user
from cards.models import BankCard
from transactions.models import CreditTransaction, Transaction
from transfers.card_payments import pay_with_card
from transfers.models import Transfer
from transfers.services import TransferError

BIG = {"single": Decimal("1000000"), "daily": Decimal("5000000")}


def make_card(account, card_type="debit", last_four="1234", **kw):
    kw.setdefault("expiry_month", 12)
    kw.setdefault("expiry_year", timezone.localdate().year + 3)
    return BankCard.objects.create(account=account, card_type=card_type, cardholder_name="Ada Obi",
                                   last_four=last_four, **kw)


class CardPaymentBase(TestCase):
    def setUp(self):
        cache.clear()
        self.alice = make_user("alice")
        self.a = make_account(self.alice, "50000", kyc_tier=1)       # tier 2: credit cards allowed
        self.a.set_pin("1234")
        self.bob = make_user("bob")
        self.b = make_account(self.bob, "1000")
        self.debit = make_card(self.a, "debit", "1234")
        self.credit = make_card(self.a, "credit", "9999")
        self.facility = CreditAccount.objects.create(account=self.a, credit_limit=Decimal("100000"), status="active")
        for target in ("transfers.card_payments.limits_for", "transfers.services.limits_for"):
            p = mock.patch(target, return_value=dict(BIG))              # the limits themselves are tested elsewhere
            p.start()
            self.addCleanup(p.stop)

    def pay(self, card=None, amount="1000", pin="1234", ref=None, receiver=None, **kw):
        return pay_with_card(self.a, receiver or self.b, (card or self.debit).pk, amount, pin, ref, **kw)

    def bal(self, acc):
        return Account.objects.get(pk=acc.pk).balance

    def owed(self):
        return CreditAccount.objects.get(pk=self.facility.pk).outstanding_balance

    def nothing_moved(self):
        self.assertEqual((self.bal(self.a), self.bal(self.b)), (Decimal("50000"), Decimal("1000")))
        self.assertEqual(self.owed(), Decimal("0"))
        self.assertFalse(Transfer.objects.exists())


class DebitCardTests(CardPaymentBase):
    def test_the_money_moves_between_balances(self):
        t = self.pay(amount="1500")
        self.assertEqual((self.bal(self.a), self.bal(self.b)), (Decimal("48500"), Decimal("2500")))
        self.assertEqual((t.funding_source, t.card_id, t.status), ("debit_card", self.debit.pk, "completed"))

    def test_the_card_is_named_in_the_sender_history(self):
        t = self.pay(note="rent")
        out = Transaction.objects.get(account=self.a, reference=t.reference)
        self.assertIn("Debit card •••• 1234", out.description)
        self.assertIn("rent", out.description)
        self.assertEqual(Transaction.objects.get(account=self.b, reference=t.reference).transaction_type, "deposit")

    def test_not_enough_balance_moves_nothing(self):
        with self.assertRaisesMessage(TransferError, "Insufficient balance"):
            self.pay(amount="60000")
        self.nothing_moved()


class CreditCardTests(CardPaymentBase):
    def test_the_sender_balance_is_untouched_and_the_debt_grows(self):
        t = self.pay(self.credit, "1000")
        self.assertEqual((self.bal(self.a), self.bal(self.b)), (Decimal("50000"), Decimal("2000")))
        self.assertEqual(self.owed(), Decimal("1000"))
        self.assertEqual((t.funding_source, t.card_id), ("credit_card", self.credit.pk))

    def test_the_debt_is_recorded_as_a_credit_purchase(self):
        t = self.pay(self.credit, "1000")
        ct = CreditTransaction.objects.get(credit_account=self.facility)
        self.assertEqual((ct.transaction_type, ct.amount, ct.reference), ("purchase", Decimal("1000"), t.reference))

    def test_the_card_is_named_in_the_sender_history(self):
        t = self.pay(self.credit)
        self.assertIn("Credit card •••• 9999", Transaction.objects.get(account=self.a, reference=t.reference).description)

    def test_a_low_tier_cannot_use_credit(self):
        Account.objects.filter(pk=self.a.pk).update(kyc_tier=0)
        with self.assertRaisesMessage(TransferError, "Verify your ID"):
            self.pay(self.credit)
        self.nothing_moved()

    @override_settings(CREDIT_P2P_SINGLE="5000", CREDIT_P2P_DAILY="8000")
    def test_the_per_payment_and_daily_caps_apply(self):
        with self.assertRaisesMessage(TransferError, "limited to ₦5,000"):
            self.pay(self.credit, "6000")
        self.pay(self.credit, "4000")
        self.pay(self.credit, "4000")
        with self.assertRaisesMessage(TransferError, "Daily credit card limit"):
            self.pay(self.credit, "1000")
        self.assertEqual(self.owed(), Decimal("8000"))

    def test_you_cannot_borrow_more_than_the_available_credit(self):
        CreditAccount.objects.filter(pk=self.facility.pk).update(credit_limit=Decimal("500"))
        with self.assertRaisesMessage(TransferError, "Insufficient available credit"):
            self.pay(self.credit, "1000")
        self.nothing_moved()

    def test_an_inactive_or_missing_facility_is_refused(self):
        CreditAccount.objects.filter(pk=self.facility.pk).update(status="suspended")
        with self.assertRaisesMessage(TransferError, "not active"):
            self.pay(self.credit)
        self.facility.delete()
        with self.assertRaisesMessage(TransferError, "no credit facility"):
            self.pay(self.credit)
        self.assertEqual((self.bal(self.a), self.bal(self.b)), (Decimal("50000"), Decimal("1000")))
        self.assertFalse(Transfer.objects.exists())

    def test_the_same_reference_spends_on_credit_only_once(self):
        first = self.pay(self.credit, "1000", ref="KLCREDIT0001")
        again = self.pay(self.credit, "1000", ref="KLCREDIT0001")
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(self.owed(), Decimal("1000"))
        self.assertEqual(CreditTransaction.objects.filter(credit_account=self.facility).count(), 1)

    def test_the_debt_can_be_repaid_with_the_normal_repayment_code(self):
        self.pay(self.credit, "1000")
        repay_credit(self.facility, "1000", pin="1234")
        self.assertEqual(self.owed(), Decimal("0"))
        self.assertEqual(self.bal(self.a), Decimal("49000"))          # repaid from the main balance

    def test_credit_is_not_limited_by_the_account_balance(self):
        Account.objects.filter(pk=self.a.pk).update(balance=Decimal("0"))
        self.pay(self.credit, "1000")
        self.assertEqual(self.bal(self.a), Decimal("0"))


class CardRulesTests(CardPaymentBase):
    def test_someone_elses_card_or_an_unknown_card_is_refused(self):
        theirs = make_card(self.b, "debit", "5555")
        for card_id in (theirs.pk, 999999):
            with self.subTest(card_id=card_id), self.assertRaisesMessage(TransferError, "can't be used"):
                pay_with_card(self.a, self.b, card_id, "1000", "1234")
        self.nothing_moved()

    def test_a_blocked_or_expired_card_is_refused(self):
        BankCard.objects.filter(pk=self.debit.pk).update(status="blocked")
        with self.assertRaisesMessage(TransferError, "not active"):
            self.pay()
        BankCard.objects.filter(pk=self.debit.pk).update(status="active", expiry_year=2020, expiry_month=1)
        with self.assertRaisesMessage(TransferError, "expired"):
            self.pay()
        self.nothing_moved()

    def test_a_card_is_valid_through_the_end_of_its_expiry_month(self):
        today = timezone.localdate()
        BankCard.objects.filter(pk=self.debit.pk).update(expiry_year=today.year, expiry_month=today.month)
        self.assertEqual(self.pay().status, "completed")

    def test_you_cannot_pay_yourself(self):
        with self.assertRaisesMessage(TransferError, "own account"):
            self.pay(receiver=self.a)

    def test_a_closed_receiver_is_refused_for_both_card_types(self):
        Account.objects.filter(pk=self.b.pk).update(closed_at=timezone.now())
        for card in (self.debit, self.credit):
            with self.subTest(card=card.card_type), self.assertRaisesMessage(TransferError, "not available"):
                self.pay(card)
        self.assertFalse(Transfer.objects.exists())

    def test_locked_transfers_are_refused(self):
        Account.objects.filter(pk=self.a.pk).update(transfers_locked_until=timezone.now() + timedelta(hours=1))
        with self.assertRaisesMessage(TransferError, "Transfers are temporarily locked"):
            self.pay()
        self.nothing_moved()

    def test_bad_amounts_are_refused_before_anything_else(self):
        for amount in ("abc", "0", "-5", "10.555", "NaN"):
            with self.subTest(amount=amount), self.assertRaises(TransferError):
                self.pay(amount=amount)
        self.nothing_moved()


class PinAndRetryTests(CardPaymentBase):
    def test_a_wrong_pin_is_counted_and_moves_nothing(self):
        with self.assertRaisesMessage(TransferError, "Incorrect transaction PIN"):
            self.pay(pin="0000")
        self.assertEqual(Account.objects.get(pk=self.a.pk).pin_attempts, 1)
        self.nothing_moved()

    def test_three_wrong_pins_lock_the_account_even_for_the_right_pin(self):
        for _ in range(2):
            with self.assertRaisesMessage(TransferError, "Incorrect"):
                self.pay(pin="0000")
        with self.assertRaisesMessage(TransferError, "temporarily locked"):
            self.pay(pin="0000")
        with self.assertRaisesMessage(TransferError, "temporarily locked"):
            self.pay(pin="1234")
        self.nothing_moved()

    def test_a_missing_or_unset_pin_is_refused(self):
        for pin in ("", "   ", None):
            with self.subTest(pin=pin), self.assertRaisesMessage(TransferError, "Enter your transaction PIN"):
                self.pay(pin=pin)
        Account.objects.filter(pk=self.a.pk).update(pin_hash="")
        with self.assertRaisesMessage(TransferError, "set your transaction PIN"):
            self.pay()

    def test_the_same_reference_pays_only_once(self):
        first = self.pay(amount="1000", ref="KLTEST000001")
        again = self.pay(amount="1000", ref="KLTEST000001")
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(self.bal(self.b), Decimal("2000"))
        self.assertEqual(Transfer.objects.count(), 1)

    def test_a_reused_reference_with_other_details_is_refused(self):
        self.pay(amount="1000", ref="KLTEST000002")
        with self.assertRaisesMessage(TransferError, "Duplicate transfer reference"):
            self.pay(amount="2000", ref="KLTEST000002")
        with self.assertRaisesMessage(TransferError, "Duplicate transfer reference"):
            self.pay(self.credit, amount="1000", ref="KLTEST000002")
        self.assertEqual(self.bal(self.b), Decimal("2000"))
