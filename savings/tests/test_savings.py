from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Sum
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Account
from accounts.services import closure_blockers
from savings.models import SavingsEntry, SavingsPlan
from savings.services import (SavingsError, accrue, break_locked, close_flex, create_plan,
                              deposit, mature_plan, run_daily, withdraw)
from transactions.models import Transaction

User = get_user_model()
_n = iter(range(1, 10**6))


def ref():
    return f"SVT{next(_n):07d}"


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("ann", password="pass12345")
        self.acc = Account.objects.create(user=self.user, account_number="1000000001",
                                          balance=Decimal("100000"))
        self.acc.set_pin("4827")

    def fresh(self):
        self.acc.refresh_from_db()
        return self.acc

    def bal(self):
        return self.fresh().balance

    def flex(self, amount="10000"):
        return create_plan(self.fresh(), name="Rent", kind="flex", amount=amount, reference=ref(), pin="4827")

    def locked(self, amount="10000", term=30):
        return create_plan(self.fresh(), name="Fixed", kind="locked", amount=amount,
                           reference=ref(), pin="4827", term_days=term)

    def total(self):
        saved = SavingsPlan.objects.aggregate(t=Sum("balance"))["t"] or Decimal("0")
        return self.bal() + saved


class FlexTests(Base):
    def test_create_moves_money_and_records_it(self):
        p = self.flex("10000")
        self.assertEqual(self.bal(), Decimal("90000"))
        self.assertEqual(p.balance, Decimal("10000"))
        self.assertEqual(SavingsEntry.objects.filter(plan=p, entry_type="deposit").count(), 1)
        self.assertTrue(Transaction.objects.filter(account=self.acc, transaction_type="savings_deposit").exists())

    def test_empty_flexible_plan_is_allowed(self):
        p = self.flex("0")
        self.assertEqual(p.balance, Decimal("0"))
        self.assertEqual(self.bal(), Decimal("100000"))

    def test_deposit_and_withdraw_keep_the_total(self):
        p = self.flex("10000")
        deposit(self.fresh(), p.pk, "5000", ref(), "4827")
        withdraw(self.fresh(), p.pk, "2000", ref(), "4827")
        p.refresh_from_db()
        self.assertEqual(p.balance, Decimal("13000"))
        self.assertEqual(self.total(), Decimal("100000"))

    def test_cannot_withdraw_more_than_the_plan_holds(self):
        p = self.flex("1000")
        with self.assertRaises(SavingsError):
            withdraw(self.fresh(), p.pk, "1000.01", ref(), "4827")
        self.assertEqual(self.total(), Decimal("100000"))

    def test_insufficient_main_balance(self):
        with self.assertRaises(SavingsError):
            self.flex("100001")
        self.assertEqual(SavingsPlan.objects.count(), 0)

    def test_bad_amounts(self):
        p = self.flex("1000")
        for amt in ["0", "-5", "abc", "NaN", "Infinity", "1e999", "10.999", "50", ""]:
            with self.assertRaises(SavingsError, msg=amt):
                deposit(self.fresh(), p.pk, amt, ref(), "4827")
        self.assertEqual(self.total(), Decimal("100000"))

    def test_wrong_pin_moves_nothing_and_locks_after_three(self):
        p = self.flex("1000")
        for _ in range(3):
            with self.assertRaises(SavingsError):
                deposit(self.fresh(), p.pk, "500", ref(), "0000")
        with self.assertRaises(SavingsError):
            deposit(self.fresh(), p.pk, "500", ref(), "4827")
        self.assertEqual(self.total(), Decimal("100000"))

    def test_same_reference_moves_money_once(self):
        p = self.flex("1000")
        r = ref()
        deposit(self.fresh(), p.pk, "500", r, "4827")
        deposit(self.fresh(), p.pk, "500", r, "4827")
        p.refresh_from_db()
        self.assertEqual(p.balance, Decimal("1500"))
        with self.assertRaises(SavingsError):
            deposit(self.fresh(), p.pk, "700", r, "4827")

    def test_creating_twice_with_one_reference_makes_one_plan(self):
        r = ref()
        for _ in range(2):
            create_plan(self.fresh(), name="A", kind="flex", amount="1000", reference=r, pin="4827")
        self.assertEqual(SavingsPlan.objects.count(), 1)
        self.assertEqual(self.bal(), Decimal("99000"))

    def test_another_users_plan_cannot_be_touched(self):
        p = self.flex("1000")
        other = User.objects.create_user("bob", password="pass12345")
        b = Account.objects.create(user=other, account_number="1000000002", balance=Decimal("5000"))
        b.set_pin("1357")
        with self.assertRaises(SavingsError):
            deposit(b, p.pk, "500", ref(), "1357")
        with self.assertRaises(SavingsError):
            close_flex(b, p.pk, ref(), "1357")

    def test_closed_account_is_refused(self):
        p = self.flex("1000")
        Account.objects.filter(pk=self.acc.pk).update(closed_at=timezone.now())
        with self.assertRaises(SavingsError):
            deposit(self.fresh(), p.pk, "500", ref(), "4827")

    def test_close_returns_everything_and_blocks_further_deposits(self):
        p = self.flex("10000")
        close_flex(self.fresh(), p.pk, ref(), "4827")
        p.refresh_from_db()
        self.assertEqual((p.status, p.balance), ("closed", Decimal("0")))
        self.assertEqual(self.bal(), Decimal("100000"))
        with self.assertRaises(SavingsError):
            deposit(self.fresh(), p.pk, "500", ref(), "4827")

    def test_plan_limit(self):
        for _ in range(10):
            self.flex("100")
        with self.assertRaises(SavingsError):
            self.flex("100")


class LockedTests(Base):
    def test_needs_a_valid_term_and_a_big_enough_amount(self):
        with self.assertRaises(SavingsError):
            self.locked(term=45)
        with self.assertRaises(SavingsError):
            self.locked(amount="999")

    def test_cannot_add_or_withdraw(self):
        p = self.locked()
        with self.assertRaises(SavingsError):
            deposit(self.fresh(), p.pk, "500", ref(), "4827")
        with self.assertRaises(SavingsError):
            withdraw(self.fresh(), p.pk, "500", ref(), "4827")

    def test_breaking_early_gives_back_principal_and_forfeits_interest(self):
        p = self.locked("10000")
        SavingsPlan.objects.filter(pk=p.pk).update(balance=Decimal("10020"))
        break_locked(self.fresh(), p.pk, ref(), "4827")
        p.refresh_from_db()
        self.assertEqual(p.status, "closed")
        self.assertEqual(self.bal(), Decimal("100000"))
        self.assertTrue(SavingsEntry.objects.filter(plan=p, entry_type="forfeit", amount=Decimal("20")).exists())

    def test_maturity_pays_principal_and_interest_once(self):
        p = self.locked("10000", 30)
        today = timezone.localdate()
        SavingsPlan.objects.filter(pk=p.pk).update(
            start_date=today - timedelta(days=30), last_accrued_on=today - timedelta(days=30),
            maturity_date=today)
        self.assertTrue(mature_plan(p.pk))
        self.assertFalse(mature_plan(p.pk))                      # a second run pays nothing
        p.refresh_from_db()
        self.assertEqual(p.status, "closed")
        self.assertEqual(self.bal(), Decimal("100057.53"))      # 10000 x 7% x 30 / 365, rounded down

    def test_nothing_happens_before_maturity(self):
        p = self.locked()
        self.assertFalse(mature_plan(p.pk))
        p.refresh_from_db()
        self.assertEqual(p.status, "active")

    def test_breaking_after_maturity_pays_in_full(self):
        p = self.locked("10000", 30)
        today = timezone.localdate()
        SavingsPlan.objects.filter(pk=p.pk).update(
            last_accrued_on=today - timedelta(days=30), maturity_date=today)
        break_locked(self.fresh(), p.pk, ref(), "4827")
        self.assertEqual(self.bal(), Decimal("100057.53"))


class InterestTests(Base):
    def aged(self, plan, days):
        plan.last_accrued_on = timezone.localdate() - timedelta(days=days)
        plan.save()
        return plan

    def test_interest_rounds_down_and_is_not_paid_twice(self):
        p = self.aged(SavingsPlan.objects.get(pk=self.flex("10000").pk), 30)
        self.assertEqual(accrue(p), Decimal("41.09"))            # 10000 x 5% x 30 / 365 = 41.0958...
        self.assertEqual(accrue(p), Decimal("0.00"))
        self.assertEqual(p.balance, Decimal("10041.09"))

    def test_a_tiny_balance_keeps_its_fraction(self):
        p = self.aged(SavingsPlan.objects.get(pk=self.flex("100").pk), 1)
        p.balance = Decimal("10")                      # 10 x 5% / 365 is under one kobo a day
        p.save()
        self.assertEqual(accrue(p), Decimal("0.00"))
        self.assertGreater(p.interest_carry, 0)         # the fraction is kept, not lost
        p.last_accrued_on = timezone.localdate() - timedelta(days=7)
        p.save()
        self.assertEqual(accrue(p), Decimal("0.01"))    # kept fraction plus 7 more days adds up to a kobo
        self.assertEqual(p.balance, Decimal("10.01"))
    def test_daily_job_is_safe_to_run_twice(self):
        p = self.aged(SavingsPlan.objects.get(pk=self.flex("10000").pk), 10)
        first = run_daily()
        p.refresh_from_db()
        after_first = p.balance
        second = run_daily()
        p.refresh_from_db()
        self.assertGreater(first["interest"], 0)
        self.assertEqual(second["interest"], Decimal("0.00"))
        self.assertEqual(p.balance, after_first)

    def test_deposit_pays_interest_to_date_first(self):
        p = self.aged(SavingsPlan.objects.get(pk=self.flex("10000").pk), 30)
        deposit(self.fresh(), p.pk, "5000", ref(), "4827")
        p.refresh_from_db()
        self.assertEqual(p.balance, Decimal("15041.09"))         # interest on 10,000 only, then the deposit


class ViewTests(Base):
    def setUp(self):
        super().setUp()
        self.client.login(username="ann", password="pass12345")

    def test_pages_need_login(self):
        for name in ("savings:home", "savings:new"):
            self.assertEqual(Client().get(reverse(name)).status_code, 302)

    def test_create_and_see_a_plan(self):
        r = self.client.post(reverse("savings:new"), {
            "name": "Phone", "kind": "flex", "term_days": "", "target_amount": "50000",
            "amount": "1000", "pin": "4827"})
        p = SavingsPlan.objects.get()
        self.assertRedirects(r, reverse("savings:detail", args=[p.pk]))
        self.assertContains(self.client.get(reverse("savings:home")), "Phone")
        self.assertEqual(self.bal(), Decimal("99000"))

    def test_locked_plan_needs_a_term(self):
        self.client.post(reverse("savings:new"), {
            "name": "Fixed", "kind": "locked", "term_days": "", "amount": "5000", "pin": "4827"})
        self.assertEqual(SavingsPlan.objects.count(), 0)

    def test_getting_a_move_page_moves_no_money(self):
        p = self.flex("1000")
        for name in ("savings:deposit", "savings:withdraw", "savings:close"):
            self.assertEqual(self.client.get(reverse(name, args=[p.pk])).status_code, 200)
        self.assertEqual(self.total(), Decimal("100000"))

    def test_deposit_through_the_page(self):
        p = self.flex("1000")
        self.client.post(reverse("savings:deposit", args=[p.pk]), {"amount": "500", "pin": "4827"})
        p.refresh_from_db()
        self.assertEqual(p.balance, Decimal("1500"))

    def test_wrong_pin_through_the_page(self):
        p = self.flex("1000")
        self.client.post(reverse("savings:deposit", args=[p.pk]), {"amount": "500", "pin": "0000"})
        p.refresh_from_db()
        self.assertEqual(p.balance, Decimal("1000"))

    def test_someone_elses_plan_is_a_404(self):
        p = self.flex("1000")
        other = User.objects.create_user("bob", password="pass12345")
        Account.objects.create(user=other, account_number="1000000002")
        c = Client()
        c.login(username="bob", password="pass12345")
        for name in ("savings:detail", "savings:deposit", "savings:withdraw", "savings:close"):
            self.assertEqual(c.get(reverse(name, args=[p.pk])).status_code, 404)

    def test_savings_block_closing_the_account(self):
        create_plan(self.fresh(), name="All", kind="flex", amount="100000", reference=ref(), pin="4827")
        self.assertTrue(any("savings" in b.lower() for b in closure_blockers(self.fresh())))