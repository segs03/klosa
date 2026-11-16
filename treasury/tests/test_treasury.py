from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Account
from transactions.models import Transaction
from treasury.models import CashOut, CashRequest
from treasury.services import (TreasuryError, approve_cash_in, create_cash_in, expire_cash_outs,
                               pay_cash_out, reject_cash_in, request_cash_out)

User = get_user_model()
_n = iter(range(1, 10**6))


def staff(*codes):
    s = User.objects.create_user(f"s{next(_n)}", password="pass12345", is_staff=True)
    s.user_permissions.add(*Permission.objects.filter(codename__in=codes))
    return User.objects.get(pk=s.pk)


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("ann", password="pass12345")
        self.acc = Account.objects.create(user=self.user, account_number="1000000001", kyc_tier=1)
        self.acc.set_pin("4827")
        self.teller = staff("record_cash", "pay_cashout")
        self.approver = staff("approve_cash")

    def bal(self):
        self.acc.refresh_from_db()
        return self.acc.balance

    def fund(self, amount="50000"):
        req = create_cash_in(self.teller, "1000000001", amount, "Ikeja")
        approve_cash_in(req.pk, self.approver)

    def out(self, amount="1000", ref=None, pin="4827"):
        return request_cash_out(Account.objects.get(pk=self.acc.pk), amount=amount,
                                reference=ref or f"CO{next(_n):010d}", pin=pin)


class CashInTests(Base):
    def test_cash_is_credited_only_after_a_second_person_approves(self):
        req = create_cash_in(self.teller, "1000000001", "20000", "Ikeja")
        self.assertEqual(self.bal(), Decimal("0"))
        done = approve_cash_in(req.pk, self.approver)
        self.assertEqual((done.status, self.bal()), ("posted", Decimal("20000")))
        self.assertTrue(Transaction.objects.filter(reference=req.reference, transaction_type="deposit").exists())

    def test_an_approved_request_cannot_be_approved_again(self):
        req = create_cash_in(self.teller, "1000000001", "20000", "Ikeja")
        approve_cash_in(req.pk, self.approver)
        with self.assertRaises(TreasuryError):
            approve_cash_in(req.pk, staff("approve_cash"))
        self.assertEqual(self.bal(), Decimal("20000"))

    def test_nobody_approves_their_own_entry_or_their_own_account(self):
        both = staff("record_cash", "approve_cash")
        req = create_cash_in(both, "1000000001", "1000", "Ikeja")
        with self.assertRaises(PermissionDenied):
            approve_cash_in(req.pk, both)
        mine = Account.objects.create(user=self.approver, account_number="1000000002")
        req2 = create_cash_in(self.teller, mine.account_number, "1000", "Ikeja")
        with self.assertRaises(PermissionDenied):
            approve_cash_in(req2.pk, self.approver)
        with self.assertRaises(TreasuryError):
            create_cash_in(self.teller, "1000000001", "1000", "")           # a branch is needed
        own = Account.objects.create(user=self.teller, account_number="1000000003")
        with self.assertRaises(TreasuryError):
            create_cash_in(self.teller, own.account_number, "1000", "Ikeja")

    def test_permissions_are_enforced(self):
        with self.assertRaises(PermissionDenied):
            create_cash_in(self.approver, "1000000001", "1000", "Ikeja")
        req = create_cash_in(self.teller, "1000000001", "1000", "Ikeja")
        for who in (self.teller, self.user):
            with self.assertRaises(PermissionDenied):
                approve_cash_in(req.pk, who)
        self.assertEqual(self.bal(), Decimal("0"))

    def test_bad_amounts_and_accounts(self):
        for amt in ["0", "-5", "abc", "NaN", "99", "5000001", "10.999", ""]:
            with self.assertRaises(TreasuryError, msg=amt):
                create_cash_in(self.teller, "1000000001", amt, "Ikeja")
        with self.assertRaises(TreasuryError):
            create_cash_in(self.teller, "9999999999", "1000", "Ikeja")
        self.assertEqual(CashRequest.objects.count(), 0)

    def test_a_closed_account_is_not_credited(self):
        req = create_cash_in(self.teller, "1000000001", "1000", "Ikeja")
        Account.objects.filter(pk=self.acc.pk).update(closed_at=timezone.now())
        self.assertEqual(approve_cash_in(req.pk, self.approver).status, "rejected")
        self.assertEqual(self.bal(), Decimal("0"))

    def test_rejecting_needs_a_reason(self):
        req = create_cash_in(self.teller, "1000000001", "1000", "Ikeja")
        with self.assertRaises(TreasuryError):
            reject_cash_in(req.pk, self.approver, " ")
        self.assertEqual(reject_cash_in(req.pk, self.approver, "Wrong amount").status, "rejected")
        self.assertEqual(self.bal(), Decimal("0"))


class CashOutTests(Base):
    def setUp(self):
        super().setUp()
        self.fund("50000")

    def test_the_money_is_taken_and_a_code_is_returned_once(self):
        co, code = self.out("10000")
        self.assertRegex(code, r"^\d{6}$")
        self.assertEqual((co.status, self.bal()), ("held", Decimal("40000")))
        self.assertNotIn(code, co.code_hash)
        self.assertTrue(Transaction.objects.filter(reference=co.reference, transaction_type="withdrawal").exists())

    def test_a_retry_takes_nothing_more_and_shows_no_code(self):
        co, _ = self.out("10000", ref="CORETRY0001")
        again, code = self.out("10000", ref="CORETRY0001")
        self.assertEqual((again.pk, code, self.bal()), (co.pk, None, Decimal("40000")))
        with self.assertRaises(TreasuryError):
            self.out("20000", ref="CORETRY0001")

    def test_wrong_pin_takes_nothing_and_three_lock_the_account(self):
        for _ in range(3):
            with self.assertRaises(TreasuryError):
                self.out(pin="0000")
        with self.assertRaises(TreasuryError):
            self.out(pin="4827")
        self.assertEqual((self.bal(), CashOut.objects.count()), (Decimal("50000"), 0))

    def test_limits_balance_tier_and_closed_account(self):
        with self.assertRaises(TreasuryError):
            self.out("50001")                                   # over the tier-1 single limit
        Account.objects.filter(pk=self.acc.pk).update(balance=Decimal("600"))
        with self.assertRaises(TreasuryError):
            self.out("700")
        Account.objects.filter(pk=self.acc.pk).update(balance=Decimal("50000"), kyc_tier=0)
        with self.assertRaises(TreasuryError):
            self.out()
        Account.objects.filter(pk=self.acc.pk).update(kyc_tier=1, closed_at=timezone.now())
        with self.assertRaises(TreasuryError):
            self.out()
        self.assertEqual(CashOut.objects.count(), 0)

    def test_the_daily_limit(self):
        for _ in range(4):
            self.out("50000" if self.bal() >= 50000 else "1000")
            Account.objects.filter(pk=self.acc.pk).update(balance=Decimal("50000"))
        with self.assertRaises(TreasuryError):
            self.out("1000")

    def test_the_teller_pays_once_with_the_right_code(self):
        co, code = self.out("10000")
        paid = pay_cash_out(self.teller, "1000000001", code)
        self.assertEqual((paid.pk, paid.status, paid.paid_by), (co.pk, "paid", self.teller))
        with self.assertRaises(TreasuryError):
            pay_cash_out(self.teller, "1000000001", code)
        self.assertEqual(self.bal(), Decimal("40000"))

    def test_wrong_codes_are_refused_and_guessing_is_stopped(self):
        _, code = self.out("1000")
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            with self.assertRaises(TreasuryError):
                pay_cash_out(self.teller, "1000000001", wrong)
        with self.assertRaises(TreasuryError):
            pay_cash_out(self.teller, "1000000001", code)       # even the right code, after too many guesses
        self.assertEqual(CashOut.objects.get().status, "held")

    def test_a_code_works_only_for_its_own_account(self):
        _, code = self.out("1000")
        other = User.objects.create_user("bob", password="x")
        Account.objects.create(user=other, account_number="1000000009")
        with self.assertRaises(TreasuryError):
            pay_cash_out(self.teller, "1000000009", code)

    def test_only_a_teller_can_pay(self):
        _, code = self.out("1000")
        for who in (self.approver, self.user):
            with self.assertRaises(PermissionDenied):
                pay_cash_out(who, "1000000001", code)

    def test_an_expired_code_is_refused_and_then_refunded_once(self):
        co, code = self.out("10000")
        CashOut.objects.filter(pk=co.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        with self.assertRaises(TreasuryError):
            pay_cash_out(self.teller, "1000000001", code)
        self.assertEqual((expire_cash_outs(), expire_cash_outs()), (1, 0))
        self.assertEqual(self.bal(), Decimal("50000"))
        self.assertTrue(Transaction.objects.filter(reference=co.reference + "R").exists())

    def test_a_paid_code_is_never_refunded(self):
        co, code = self.out("10000")
        pay_cash_out(self.teller, "1000000001", code)
        CashOut.objects.filter(pk=co.pk).update(expires_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(expire_cash_outs(), 0)
        self.assertEqual(self.bal(), Decimal("40000"))


class AuditTests(Base):
    def test_balances_match_after_real_flows(self):
        self.fund("50000")
        co, code = self.out("10000")
        pay_cash_out(self.teller, "1000000001", code)
        call_command("audit_balances")                           # passes

    def test_a_tampered_balance_is_caught(self):
        self.fund("50000")
        Account.objects.filter(pk=self.acc.pk).update(balance=Decimal("99999"))
        with self.assertRaises(CommandError):
            call_command("audit_balances")

    def test_a_negative_balance_is_caught(self):
        Account.objects.filter(pk=self.acc.pk).update(balance=Decimal("-1"))
        with self.assertRaises(CommandError):
            call_command("audit_balances")


class ViewTests(Base):
    def setUp(self):
        super().setUp()
        self.fund("50000")
        self.client.login(username="ann", password="pass12345")

    def test_the_customer_sees_the_code_once(self):
        r = self.client.post(reverse("treasury:cashout"), {"amount": "2000", "pin": "4827"})
        self.assertEqual(r.status_code, 200)
        self.assertRegex(r.context["code"], r"^\d{6}$")
        self.assertEqual(self.bal(), Decimal("48000"))

    def test_a_wrong_pin_shows_an_error_and_takes_nothing(self):
        self.client.post(reverse("treasury:cashout"), {"amount": "2000", "pin": "0000"})
        self.assertEqual((self.bal(), CashOut.objects.count()), (Decimal("50000"), 0))

    def test_teller_pages_are_for_staff_only(self):
        for name in ("treasury:pay", "treasury:cash_in"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)
            self.assertEqual(Client().get(reverse(name)).status_code, 302)
        c = Client()
        c.login(username=self.teller.username, password="pass12345")
        self.assertEqual(c.get(reverse("treasury:pay")).status_code, 200)
        self.assertEqual(c.get(reverse("treasury:cash_in")).status_code, 200)
        c2 = Client()
        c2.login(username=self.approver.username, password="pass12345")
        self.assertEqual(c2.get(reverse("treasury:pay")).status_code, 403)

    def test_the_teller_pays_through_the_page(self):
        _, code = self.out("1000")
        c = Client()
        c.login(username=self.teller.username, password="pass12345")
        c.post(reverse("treasury:pay"), {"account_number": "1000000001", "code": code})
        self.assertEqual(CashOut.objects.get().status, "paid")