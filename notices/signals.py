from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save, pre_save
from django.urls import reverse
from django.utils import timezone

from accounts.kyc_services import DOC_TYPES, verified_types
from accounts.models import Account, CreditAccount, CreditApplication, KYCApplication, CreditSettlement
from face_auth.models import FaceSession
from biller.models import BillPayment
from cards.models import BankCard, CardOrder
from cashier.models import Deposit, Withdrawal
from transactions.models import CreditTransaction, Transaction
from transfers.models import MoneyRequest
from treasury.models import CashOut, CashRequest
from .services import notify_user

from funding.models import ContributionIntent


User = get_user_model()


def naira(v):
    return f"₦{Decimal(str(v)):,.2f}"


def who(u):
    return u.get_full_name().strip() or "A Klosa customer"


def watch(model, field, handler):
    """Call handler(obj, old, new, created) when `field` is created or changes."""
    uid = f"notice_{model.__name__}_{field}"
    old_attr, skip_attr = f"_notice_old_{field}", f"_notice_skip_{field}"

    def before(sender, instance, **kw):
        uf = kw.get("update_fields")
        skip = uf is not None and field not in uf
        setattr(instance, skip_attr, skip)
        setattr(instance, old_attr, None)
        if not skip and instance.pk:
            setattr(instance, old_attr, sender._default_manager.filter(pk=instance.pk).values_list(field, flat=True).first())

    def after(sender, instance, created, **kw):
        if kw.get("raw") or getattr(instance, skip_attr, True):
            return
        old, new = getattr(instance, old_attr, None), getattr(instance, field)
        if created or old != new:
            handler(instance, old, new, created)

    pre_save.connect(before, sender=model, weak=False, dispatch_uid=uid + "_pre")
    post_save.connect(after, sender=model, weak=False, dispatch_uid=uid + "_post")


# ---------------------------------------------------------- every money movement
def _transaction(t, created):
    if not created:
        return
    user, amt, d = t.account.user, t.amount, t.description or ""
    home = reverse("accounts:home")
    kind = t.transaction_type
    if kind == "deposit":
        if d.startswith("Welcome bonus"):
            notify_user(user, "Welcome gift", f"{naira(amt)} was added to your account.", "success", home)
        elif d.startswith("Refund:"):
            notify_user(user, "Refund received", f"{naira(amt)} · {d[7:].strip()}", "success", home)
        else:
            notify_user(user, "Credit alert", f"{naira(amt)} · {d}", "success", home)
    elif kind in ("transfer", "withdrawal", "payment"):
        notify_user(user, "Debit alert", f"{naira(amt)} · {d}", "info", home)
    elif kind == "savings_withdrawal" and d.startswith("Savings matured"):
        notify_user(user, "Savings matured", f"{naira(amt)} is back in your account. {d}", "success", reverse("savings:home"))


post_save.connect(lambda sender, instance, created, **kw: None if kw.get("raw") else _transaction(instance, created),
                  sender=Transaction, weak=False, dispatch_uid="notice_transaction")


def _credit_tx(c, created):
    if not created:
        return
    user = c.credit_account.account.user
    link = reverse("accounts:home") + "#card"
    if c.transaction_type == "purchase":
        left = c.credit_account.available_credit
        notify_user(user, "Card purchase", f"{naira(c.amount)} · {c.description}. Available credit {naira(left)}.", "info", link)
    elif c.transaction_type == "refund":
        notify_user(user, "Card refund", f"{naira(c.amount)} · {c.description}", "success", link)


post_save.connect(lambda sender, instance, created, **kw: None if kw.get("raw") else _credit_tx(instance, created),
                  sender=CreditTransaction, weak=False, dispatch_uid="notice_credit_tx")


# ------------------------------------------------------------------ status changes
def _kyc(app, old, new, created):
    u, link = app.user, reverse("accounts:verify")
    if created:
        notify_user(u, "ID received", "We are reviewing it. We will tell you the result here.", "info", link)
    elif new == "verified":
        missing = {"nin", "bvn"} - verified_types(u)
        label = DOC_TYPES.get(app.document_type, (app.document_type,))[0]
        tail = ""
        if missing and getattr(settings, "WELCOME_BONUS_ENABLED", False):
            tail = f" Verify your {' and '.join(sorted(m.upper() for m in missing))} to get your welcome gift."
        notify_user(u, "ID verified", f"Your {label} is verified.{tail}", "success", link)
    elif new in ("rejected", "additional_info"):
        r = app.reviews.order_by("-reviewed_at").first()
        title = "We could not verify your ID" if new == "rejected" else "We need more information"
        notify_user(u, title, (r.reason if r else "") or "Open the page for details.", "warning", link)


def _credit_app(a, old, new, created):
    link = reverse("accounts:credit")
    if created:
        notify_user(a.user, "Credit request received", f"You asked for {naira(a.requested_amount)}. We will review it.", "info", link)
    elif new == "approved" and a.assessed_amount:
        notify_user(a.user, "Credit approved", f"Your credit line of {naira(a.assessed_amount)} is open.", "success", link)
    elif new == "rejected":
        notify_user(a.user, "Credit request declined", a.decision_reason or "Open the page for details.", "warning", link)


def _card_order(o, old, new, created):
    u, link = o.account.user, reverse("accounts:home") + "#card"
    if created:
        notify_user(u, "Card request received", f"Your {o.get_card_type_display().lower()} request is waiting for review.", "info", link)
    elif new == "approved":
        notify_user(u, "Card request approved", "We are preparing your card.", "success", link)
    elif new == "rejected":
        notify_user(u, "Card request declined", o.rejection_reason or "Open the Cards tab for details.", "warning", link)
    elif new == "issued":
        notify_user(u, "Your card is ready", "Open the Cards tab to see it.", "success", link)


def _money_request(r, old, new, created):
    if created:
        note = f' "{r.note}"' if r.note else ""
        notify_user(r.recipient, "Money request", f"{who(r.requester)} asks for {naira(r.amount)}.{note}", "info", reverse("transfers:requests"))
    elif new == "paid":
        notify_user(r.requester, "Request paid", f"{who(r.recipient)} paid your {naira(r.amount)} request.", "success", reverse("accounts:home"))
    elif new == "declined":
        notify_user(r.requester, "Request declined", f"{who(r.recipient)} declined your {naira(r.amount)} request.", "warning", reverse("accounts:home"))


def _bill(b, old, new, created):
    if new == "completed" and not created:
        notify_user(b.account.user, "Purchase complete", f"{b.get_category_display()} · {b.provider_code.upper()} ••••{b.customer_ref[-4:]}",
                    "success", reverse("biller:order", args=[b.reference]))


def _withdrawal(w, old, new, created):
    if new == "completed":
        notify_user(w.account.user, "Withdrawal sent", f"{naira(w.amount)} to {w.bank_name} ••••{w.account_number[-4:]}.", "success", reverse("accounts:home"))


def _deposit(d, old, new, created):
    u, link = d.account.user, reverse("accounts:home")
    if new == "failed":
        notify_user(u, "Payment not completed", f"Your {naira(d.amount)} payment did not go through. No money was taken.", "warning", link)
    elif new == "review":
        notify_user(u, "Payment under review", f"We received a payment of {naira(d.amount)} that needs a quick check.", "warning", link)


def _cash_out(c, old, new, created):
    if new == "paid":
        notify_user(c.account.user, "Cash collected", f"{naira(c.amount)} was paid out to you in cash.", "success", reverse("accounts:home"))


def _cash_request(c, old, new, created):
    if new == "rejected":
        notify_user(c.account.user, "Cash deposit not accepted", f"A cash deposit of {naira(c.amount)} was not accepted. {c.reason}".strip(), "warning", reverse("accounts:home"))


def _credit_account(c, old, new, created):
    if new == "suspended" and not created:
        notify_user(c.account.user, "Credit suspended", "Your credit facility is suspended. You can still repay.", "alert", reverse("accounts:credit"))


def _bank_card(c, old, new, created):
    if new == "blocked" and not created:
        notify_user(c.account.user, "Card blocked", f"Your card ending {c.last_four} was blocked.", "alert", reverse("accounts:home") + "#card")


def _pin_lock(acc, old, new, created):
    if new and new > timezone.now() and old != new:
        notify_user(acc.user, "Transaction PIN locked", "Too many wrong tries. Try again later. If this was not you, contact support.", "alert", reverse("accounts:profile"))


def _welcome(u, old, new, created):
    if new and not created:
        notify_user(u, "Welcome to Klosa", "Your phone is verified. Set your PIN and add fingerprint login.", "success", reverse("accounts:onboarding"))


def _face(c, old, new, created):
    link = reverse("face_auth:begin", args=["upgrade"])
    if new == "review":
        notify_user(c.user, "Face check received", "We are comparing your photos with your ID.", "info", link)
    elif new == "passed":
        notify_user(c.user, "Face verified", "Your face check passed.", "success", link)
    elif new == "failed":
        notify_user(c.user, "Face check not accepted", c.reason or "Open the page for details.", "warning", link)


def _tier(acc, old, new, created):
        if not created and old is not None and new > old:
            notify_user(acc.user, "Tier upgraded", f"You are now Tier {new + 1}. Your limits are higher.", "success", reverse("accounts:upgrade"))
            
            
def _intent(i, old, new, created):
    link = reverse("funding:my_deposits")
    if new == "received":
        notify_user(i.investor.user, "Deposit received", f"Your {naira(i.amount)} deposit was received and is being verified.", "success", link)
    elif new == "rejected":
        notify_user(i.investor.user, "Deposit not accepted", i.note or "Open the page for details.", "warning", link)


def _settle(s, old, new, created):
    if new == "approved":
        notify_user(s.credit_account.account.user, "Credit cleared", f"{naira(s.amount)} of your credit was cleared. Your available credit is restored.", "success", reverse("accounts:credit"))



watch(CreditSettlement, "status", _settle)
watch(ContributionIntent, "status", _intent)
watch(Account, "kyc_tier", _tier)
watch(FaceSession, "status", _face)
watch(KYCApplication, "status", _kyc)
watch(CreditApplication, "status", _credit_app)
watch(CardOrder, "status", _card_order)
watch(MoneyRequest, "status", _money_request)
watch(BillPayment, "status", _bill)
watch(Withdrawal, "status", _withdrawal)
watch(Deposit, "status", _deposit)
watch(CashOut, "status", _cash_out)
watch(CashRequest, "status", _cash_request)
watch(CreditAccount, "status", _credit_account)
watch(BankCard, "status", _bank_card)
watch(Account, "locked_until", _pin_lock)
watch(User, "phone_verified", _welcome)