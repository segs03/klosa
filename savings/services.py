import logging
import uuid
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import Account
from transactions.models import Transaction
from . import rules
from .models import SavingsEntry, SavingsPlan

from datetime import timedelta



log = logging.getLogger(__name__)
CENT = Decimal("0.01")


class SavingsError(Exception):
    pass


def new_reference():
    return "SV" + uuid.uuid4().hex[:10].upper()


def clean_amount(value, minimum=None, allow_zero=False):
    try:
        amount = Decimal(str(value if value not in (None, "") else 0))
        ok = amount.is_finite() and amount >= 0 and amount == amount.quantize(CENT)
    except (InvalidOperation, ValueError):
        ok = False
    if not ok:
        raise SavingsError("Enter a valid amount with at most 2 decimal places.")
    if amount == 0 and allow_zero:
        return amount
    if amount <= 0:
        raise SavingsError("Enter an amount above zero.")
    if minimum and amount < minimum:
        raise SavingsError(f"The smallest amount is ₦{minimum:,.0f}.")
    return amount


def clean_name(raw):
    name = " ".join((raw or "").split())
    if not 1 <= len(name) <= 40:
        raise SavingsError("Give your plan a name (up to 40 characters).")
    return name


def _entry(plan, kind, amount, ref):
    SavingsEntry.objects.create(plan=plan, entry_type=kind, amount=amount,
                                balance_after=plan.balance, reference=ref)


def _main_tx(acc, kind, amount, ref, text):
    Transaction.objects.create(account=acc, transaction_type=kind, amount=amount,
                               reference=ref, description=text[:255])


def _run(account_pk, plan_pk, pin, work):
    """Lock the account (then the plan), check the PIN on the locked row, then do the work.
    A wrong PIN is saved and then reported, so the 3-try lock really works."""
    bad, result = None, None
    try:
        with transaction.atomic():
            acc = Account.objects.select_for_update().get(pk=account_pk)
            plan = None
            if plan_pk is not None:
                try:
                    plan = SavingsPlan.objects.select_for_update().get(pk=plan_pk, account_id=account_pk)
                except SavingsPlan.DoesNotExist:
                    raise SavingsError("Savings plan not found.")
            if acc.closed_at:
                raise SavingsError("Your account is closed.")
            if acc.is_locked():
                raise SavingsError("Your account is temporarily locked.")
            if not acc.pin_hash:
                raise SavingsError("Please set your transaction PIN first.")
            if not acc.check_pin(str(pin or "")):
                bad = "Your account is temporarily locked." if acc.is_locked() else "Incorrect transaction PIN."
            else:
                result = work(acc, plan)
    except IntegrityError:
        raise SavingsError("Duplicate reference.")
    if bad:
        raise SavingsError(bad)
    return result


def accrue(plan, as_of=None):
    """The plan must be locked. Adds interest for the days since last time. Rounds down and
    carries the fraction, so small balances still earn and the bank never overpays."""
    as_of = as_of or timezone.localdate()
    if plan.maturity_date and as_of > plan.maturity_date:
        as_of = plan.maturity_date
    days = (as_of - plan.last_accrued_on).days
    if plan.status != "active" or days <= 0:
        return Decimal("0.00")
    raw = plan.interest_carry + plan.balance * plan.rate * days / Decimal(36500)
    credit = raw.quantize(CENT, rounding=ROUND_DOWN)
    plan.interest_carry = (raw - credit).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    plan.last_accrued_on = as_of
    if credit > 0:
        plan.balance += credit
        _entry(plan, "interest", credit, f"I{plan.pk}-{as_of:%Y%m%d}")
    plan.save(update_fields=["balance", "interest_carry", "last_accrued_on"])
    return credit


def _close(plan):
    plan.balance, plan.interest_carry = Decimal("0.00"), Decimal("0")
    plan.status, plan.closed_at = "closed", timezone.now()
    plan.save(update_fields=["balance", "interest_carry", "status", "closed_at"])


def _pay_out_matured(acc, plan, ref, as_of):
    accrue(plan, plan.maturity_date)
    payout = plan.balance
    acc.balance += payout
    acc.save(update_fields=["balance"])
    plan.balance = Decimal("0.00")
    _entry(plan, "maturity", payout, ref)
    _main_tx(acc, "savings_withdrawal", payout, ref, f"Savings matured: {plan.name}")
    _close(plan)


# ---------------------------------------------------------------- what the user can do
def create_plan(account, *, name, kind, amount, reference, pin, term_days=None, target=None):
    name = clean_name(name)
    if kind == "locked":
        if term_days not in rules.LOCKED_TERMS:
            raise SavingsError("Choose a term.")
        amount = clean_amount(amount, rules.MIN_LOCKED)
    elif kind == "flex":
        amount = clean_amount(amount, rules.MIN_FLEX, allow_zero=True)
    else:
        raise SavingsError("Choose a plan type.")
    target = clean_amount(target) if target not in (None, "") else None
    if not reference or len(reference) > 20:
        raise SavingsError("Missing reference.")

    def work(acc, _):
        existing = SavingsPlan.objects.filter(reference=reference).first()
        if existing:                                         # a retry: no second plan, no second charge
            if existing.account_id == acc.pk and existing.kind == kind:
                return existing
            raise SavingsError("Duplicate reference.")
        if SavingsPlan.objects.filter(account=acc, status="active").count() >= rules.MAX_ACTIVE_PLANS:
            raise SavingsError(f"You can have up to {rules.MAX_ACTIVE_PLANS} active plans.")
        if acc.balance < amount:
            raise SavingsError("Insufficient balance.")
        today = timezone.localdate()
        locked = kind == "locked"
        plan = SavingsPlan.objects.create(
            account=acc, name=name, kind=kind, reference=reference, target_amount=target,
            balance=amount, principal=amount if locked else Decimal("0.00"),
            rate=rules.LOCKED_TERMS[term_days] if locked else rules.FLEX_RATE,
            term_days=term_days if locked else None, start_date=today,
            maturity_date=today + timedelta(days=term_days) if locked else None,
            last_accrued_on=today)
        if amount > 0:
            acc.balance -= amount
            acc.save(update_fields=["balance"])
            _entry(plan, "deposit", amount, reference)
            _main_tx(acc, "savings_deposit", amount, reference, f"Moved to savings: {name}")
        return plan

    return _run(account.pk, None, pin, work)


def deposit(account, plan_pk, amount, reference, pin):
    amount = clean_amount(amount, rules.MIN_FLEX)

    def work(acc, plan):
        existing = SavingsEntry.objects.filter(reference=reference).first()
        if existing:
            if (existing.plan_id, existing.entry_type, existing.amount) == (plan.pk, "deposit", amount):
                return plan
            raise SavingsError("Duplicate reference.")
        if plan.status != "active":
            raise SavingsError("This plan is closed.")
        if plan.kind != "flex":
            raise SavingsError("You can't add money to a locked plan.")
        if acc.balance < amount:
            raise SavingsError("Insufficient balance.")
        accrue(plan)
        acc.balance -= amount
        plan.balance += amount
        acc.save(update_fields=["balance"])
        plan.save(update_fields=["balance"])
        _entry(plan, "deposit", amount, reference)
        _main_tx(acc, "savings_deposit", amount, reference, f"Moved to savings: {plan.name}")
        return plan

    return _run(account.pk, plan_pk, pin, work)


def withdraw(account, plan_pk, amount, reference, pin):
    amount = clean_amount(amount)

    def work(acc, plan):
        existing = SavingsEntry.objects.filter(reference=reference).first()
        if existing:
            if (existing.plan_id, existing.entry_type, existing.amount) == (plan.pk, "withdrawal", amount):
                return plan
            raise SavingsError("Duplicate reference.")
        if plan.status != "active":
            raise SavingsError("This plan is closed.")
        if plan.kind != "flex":
            raise SavingsError("A locked plan can only be paid out at maturity, or broken early.")
        accrue(plan)
        if amount > plan.balance:
            raise SavingsError(f"You only have ₦{plan.balance:,.2f} in this plan.")
        plan.balance -= amount
        acc.balance += amount
        acc.save(update_fields=["balance"])
        plan.save(update_fields=["balance"])
        _entry(plan, "withdrawal", amount, reference)
        _main_tx(acc, "savings_withdrawal", amount, reference, f"Moved from savings: {plan.name}")
        return plan

    return _run(account.pk, plan_pk, pin, work)


def close_flex(account, plan_pk, reference, pin):
    def work(acc, plan):
        if plan.status == "closed":
            return plan
        if plan.kind != "flex":
            raise SavingsError("This is a locked plan.")
        accrue(plan)
        amount = plan.balance
        if amount > 0:
            acc.balance += amount
            acc.save(update_fields=["balance"])
            plan.balance = Decimal("0.00")
            _entry(plan, "withdrawal", amount, reference)
            _main_tx(acc, "savings_withdrawal", amount, reference, f"Moved from savings: {plan.name}")
        _close(plan)
        return plan

    return _run(account.pk, plan_pk, pin, work)


def break_locked(account, plan_pk, reference, pin):
    """Early break: the principal comes back and all the interest is given up."""
    def work(acc, plan):
        if plan.status == "closed":
            return plan
        if plan.kind != "locked":
            raise SavingsError("This is not a locked plan.")
        today = timezone.localdate()
        if today >= plan.maturity_date:                       # it has matured: pay it out in full
            _pay_out_matured(acc, plan, reference, today)
            return plan
        forfeited = plan.balance - plan.principal
        if forfeited > 0:
            plan.balance -= forfeited
            _entry(plan, "forfeit", forfeited, reference + "F")
        payout = plan.principal
        acc.balance += payout
        acc.save(update_fields=["balance"])
        plan.balance = Decimal("0.00")
        _entry(plan, "withdrawal", payout, reference)
        _main_tx(acc, "savings_withdrawal", payout, reference, f"Locked plan broken early: {plan.name}")
        _close(plan)
        return plan

    return _run(account.pk, plan_pk, pin, work)


# ---------------------------------------------------------------- the daily job
def mature_plan(plan_pk, as_of=None):
    as_of = as_of or timezone.localdate()
    acc_id = SavingsPlan.objects.filter(pk=plan_pk).values_list("account_id", flat=True).first()
    if acc_id is None:
        return False
    with transaction.atomic():
        acc = Account.objects.select_for_update().get(pk=acc_id)          # account first, then the plan
        plan = SavingsPlan.objects.select_for_update().get(pk=plan_pk)
        if plan.status != "active" or plan.kind != "locked" or plan.maturity_date > as_of:
            return False
        _pay_out_matured(acc, plan, f"M{plan.pk}", as_of)
    return True


def accrue_one(plan_pk, as_of=None):
    acc_id = SavingsPlan.objects.filter(pk=plan_pk).values_list("account_id", flat=True).first()
    if acc_id is None:
        return Decimal("0.00")
    with transaction.atomic():
        Account.objects.select_for_update().get(pk=acc_id)
        plan = SavingsPlan.objects.select_for_update().get(pk=plan_pk)
        return accrue(plan, as_of)


def run_daily(as_of=None):
    as_of = as_of or timezone.localdate()
    matured = interest_plans = 0
    total = Decimal("0.00")
    due = SavingsPlan.objects.filter(status="active", kind="locked", maturity_date__lte=as_of)
    for pk in list(due.values_list("pk", flat=True)):
        if mature_plan(pk, as_of):
            matured += 1
    open_plans = SavingsPlan.objects.filter(status="active", last_accrued_on__lt=as_of)
    for pk in list(open_plans.values_list("pk", flat=True)):
        try:
            got = accrue_one(pk, as_of)
        except Exception:
            log.exception("Interest failed for plan %s", pk)
            continue
        if got > 0:
            interest_plans += 1
            total += got
    return {"matured": matured, "plans_paid": interest_plans, "interest": total}