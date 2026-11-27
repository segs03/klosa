"""Fixtures for the assetfund tests. Not a test module itself.

Defaults used throughout: gold worth 900,000 verified, 40% advance = 360,000 in three stages
(100,000 / 160,000 / 100,000), fee 10% = 36,000, Klosa's share 50% of each sale, term 180 days.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone

from accounts.models import Account
from funding.models import FundingPeriod, JournalLine, LedgerAccount
from transactions.models import Transaction

from assetfund import services as svc

User = get_user_model()
D = Decimal
CFG = dict(ASSETFUND_TOTAL_CAP="10000000", ASSETFUND_CUSTOMER_CAP="2000000", ASSETFUND_MIN_KYC_TIER=2,
           ASSETFUND_MAX_FEE_PCT="15", ASSETFUND_OFFER_DAYS=7, FACE_ENFORCE=set())
STAGES = [("Buy moulds and tools", "100000"), ("Labour and casting", "160000"), ("Finishing and polish", "100000")]


def make_user(name, perms=(), staff=False):
    user = User.objects.create_user(name, password="pass12345", is_staff=staff)
    for code in perms:
        user.user_permissions.add(Permission.objects.get(codename=code))
    return user


def make_account(user, balance="0.00", tier=2):
    acc = Account.objects.create(user=user, account_number=f"{user.pk:010d}", balance=D(balance), kyc_tier=tier)
    acc.set_pin("1234")
    acc.save()
    return acc


def seed_ledger():
    for code, name, kind in [("1000", "Operating cash", "asset"), ("1150", "Asset advances receivable", "asset"),
                             ("2300", "Customer deposits", "liability"), ("4100", "Asset advance fee income", "income")]:
        LedgerAccount.objects.get_or_create(code=code, defaults={"name": name, "account_type": kind})


def open_period():
    today = timezone.localdate()
    return FundingPeriod.objects.create(start_date=today - timedelta(days=5), end_date=today + timedelta(days=25))


def journal(reference):
    """{(ledger code, 'debit' | 'credit'): amount} for one journal entry."""
    out = {}
    for line in JournalLine.objects.filter(entry__reference=reference).select_related("account"):
        side, amount = ("debit", line.debit) if line.debit else ("credit", line.credit)
        out[(line.account.code, side)] = amount
    return out


class AssetCase(TestCase):
    def setUp(self):
        seed_ledger()
        open_period()
        self.cust = make_user("ada")
        self.acc = make_account(self.cust)
        self.assessor = make_user("assessor", ["assess_assetapplication", "release_advancestage"], staff=True)
        self.approver = make_user("approver", ["approve_assetapplication"], staff=True)
        self.teller = make_user("teller", ["release_advancestage", "confirm_assetsale"], staff=True)

    # ---- the steps of the flow
    def apply(self, user=None, **kw):
        data = dict(kind="gold", title="Raw gold for jewellery", asset_ref="ASSAY-1001",
                    description="Twenty four carat gold bars held in the vault",
                    declared_value="1000000", requested_amount="400000",
                    purpose="Mould the gold into jewellery for a wedding order")
        data.update(kw)
        return svc.submit_application(user or self.cust, **data)

    def assess(self, app, staff=None, **kw):
        data = dict(verified_value="900000", advance_rate="40", fee_pct="10", share_pct="50", term_days=180,
                    stages=STAGES, note="Inspected and assayed, 24 carat, 450g")
        data.update(kw)
        return svc.assess_application(app.pk, staff or self.assessor, **data)

    def approve(self, app, staff=None):
        return svc.decide_application(app.pk, staff or self.approver, "approved", "Value confirmed")

    def accept(self, app, pin="1234"):
        return svc.accept_offer(app.user, app.pk, pin)

    def activate(self):
        app = self.apply()
        self.assess(app)
        self.approve(app)
        self.accept(app)
        app.refresh_from_db()
        return app

    # ---- money helpers
    def release(self, app, order):
        stage = app.stages.get(order=order)
        if order > 1:
            svc.submit_stage_evidence(app.user, stage.pk, "Earlier stage finished, photos kept")
        return svc.release_stage(stage.pk, self.teller)

    def fund(self, amount):
        """Cash the customer received from a sale: a deposit with its Transaction row."""
        acc = Account.objects.get(pk=self.acc.pk)
        acc.balance += D(amount)
        acc.save(update_fields=["balance"])
        Transaction.objects.create(account=acc, transaction_type="deposit", amount=D(amount),
                                   reference=f"DEP{Transaction.objects.count()}", description="Sale proceeds")

    def balance(self):
        return Account.objects.get(pk=self.acc.pk).balance
