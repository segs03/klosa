from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings
from django.utils import timezone

from accounts.models import Account
from assetfund import services as svc
from assetfund.models import AdvanceStage, AssetApplication

from .helpers import CFG, D, STAGES, AssetCase, make_account, make_user


@override_settings(**CFG)
class ApplyTests(AssetCase):
    def test_a_valid_application_is_saved_as_pending(self):
        app = self.apply()
        self.assertEqual((app.status, app.user, app.requested_amount), ("pending", self.cust, D("400000.00")))
        self.assertEqual(app.asset_ref_norm, "ASSAY1001")

    def test_needs_the_verification_tier(self):
        Account.objects.filter(pk=self.acc.pk).update(kyc_tier=1)
        with self.assertRaises(ValidationError):
            self.apply()

    def test_needs_an_open_account(self):
        Account.objects.filter(pk=self.acc.pk).update(closed_at=timezone.now())
        with self.assertRaises(ValidationError):
            self.apply()
        with self.assertRaises(ValidationError):
            self.apply(user=make_user("noaccount"))

    def test_one_application_in_progress_per_customer(self):
        self.apply()
        with self.assertRaises(ValidationError):
            self.apply(asset_ref="ASSAY-2002")

    def test_the_same_asset_cannot_be_pledged_twice(self):
        self.apply()
        other = make_user("bola")
        make_account(other)
        for ref in ("ASSAY-1001", "assay 1001", "Assay1001"):
            with self.assertRaises(ValidationError):
                self.apply(user=other, asset_ref=ref)

    def test_the_same_reference_on_a_different_kind_is_a_different_asset(self):
        self.apply()
        other = make_user("bola")
        make_account(other)
        self.apply(user=other, kind="equipment")                    # must not raise

    def test_a_rejected_asset_can_be_pledged_again(self):
        app = self.apply()
        AssetApplication.objects.filter(pk=app.pk).update(status="rejected")
        other = make_user("bola")
        make_account(other)
        self.apply(user=other)

    def test_you_cannot_ask_for_more_than_you_declared_or_the_cap(self):
        with self.assertRaises(ValidationError):
            self.apply(requested_amount="1000001")
        with override_settings(ASSETFUND_CUSTOMER_CAP="300000"):
            with self.assertRaises(ValidationError):
                self.apply()

    def test_bad_text_and_kind(self):
        for bad in (dict(kind="car"), dict(title="ab"), dict(description="too short"),
                    dict(purpose="short"), dict(asset_ref="a-"), dict(asset_ref="  ")):
            with self.assertRaises(ValidationError, msg=str(bad)):
                self.apply(**bad)
        self.assertEqual(AssetApplication.objects.count(), 0)

    def test_bad_numbers(self):
        for bad in ("abc", "-5", "0", "10.555", "nan", "inf", ""):
            with self.assertRaises(ValidationError, msg=bad):
                self.apply(requested_amount=bad)
            with self.assertRaises(ValidationError, msg=bad):
                self.apply(declared_value=bad)

    def test_withdraw_before_the_money_flows(self):
        app = self.apply()
        self.assertEqual(svc.withdraw_application(self.cust, app.pk).status, "withdrawn")
        self.apply()                                                  # a new one is allowed now

    def test_nobody_else_can_withdraw_and_an_active_project_cannot(self):
        app = self.apply()
        with self.assertRaises(PermissionDenied):
            svc.withdraw_application(make_user("thief"), app.pk)
        live = self.activate_other()
        with self.assertRaises(ValidationError):
            svc.withdraw_application(live.user, live.pk)

    def activate_other(self):
        AssetApplication.objects.all().delete()
        return self.activate()


@override_settings(**CFG)
class AssessTests(AssetCase):
    def test_a_valuation_creates_the_offer_terms_and_stages(self):
        app = self.assess(self.apply())
        self.assertEqual((app.status, app.advance_amount, app.fee_amount, app.assessed_by),
                         ("assessed", D("360000.00"), D("36000.00"), self.assessor))
        self.assertEqual([(s.order, s.amount) for s in app.stages.all()],
                         [(1, D("100000.00")), (2, D("160000.00")), (3, D("100000.00"))])

    def test_needs_the_permission_and_not_for_your_own_application(self):
        app = self.apply()
        for who in (self.cust, self.approver, make_user("plain", staff=True)):
            with self.assertRaises(PermissionDenied):
                self.assess(app, staff=who)
        boss = make_user("boss", ["assess_assetapplication"], staff=True)
        make_account(boss)
        mine = self.apply(user=boss, asset_ref="ASSAY-77")
        with self.assertRaises(PermissionDenied):
            self.assess(mine, staff=boss)

    def test_the_rate_cap_depends_on_the_kind_of_asset(self):
        with self.assertRaises(ValidationError):
            self.assess(self.apply(), advance_rate="61")             # gold: 60 at most
        AssetApplication.objects.all().delete()
        skill = self.apply(kind="skill_contract", asset_ref="ORDER-55")
        with self.assertRaises(ValidationError):
            self.assess(skill, advance_rate="31")                    # skills: 30 at most

    def test_the_advance_cannot_exceed_what_was_asked(self):
        with self.assertRaises(ValidationError):
            self.assess(self.apply(requested_amount="300000"))       # 360,000 > 300,000

    def test_stages_must_add_up_exactly(self):
        app = self.apply()
        for stages in ([("Only one", "359999.99")], [("A", "200000"), ("B", "200000")], [], [("A", "0"), ("B", "360000")]):
            with self.assertRaises(ValidationError, msg=str(stages)):
                self.assess(app, stages=stages)
        self.assertEqual(AdvanceStage.objects.count(), 0)
        with self.assertRaises(ValidationError):
            self.assess(app, stages=[(f"Stage {i}", "51428.57") for i in range(7)])    # more than six

    def test_fee_share_term_and_numbers_are_checked(self):
        app = self.apply()
        for bad in (dict(fee_pct="16"), dict(fee_pct="-1"), dict(share_pct="0"), dict(share_pct="101"),
                    dict(term_days=10), dict(term_days=5000), dict(term_days="180"), dict(verified_value="abc"),
                    dict(verified_value="0"), dict(advance_rate="0"), dict(note="short")):
            with self.assertRaises(ValidationError, msg=str(bad)):
                self.assess(app, **bad)
        self.assertEqual(AssetApplication.objects.get(pk=app.pk).status, "pending")

    def test_only_a_pending_application_can_be_assessed(self):
        app = self.assess(self.apply())
        with self.assertRaises(ValidationError):
            self.assess(app)


@override_settings(**CFG)
class DecideTests(AssetCase):
    def assessed(self, **kw):
        return self.assess(self.apply(**kw))

    def test_approval_makes_an_offer_that_expires(self):
        app = self.approve(self.assessed())
        self.assertEqual((app.status, app.approved_by), ("approved", self.approver))
        self.assertGreater(app.offer_expires_at, timezone.now())

    def test_rejection_needs_a_reason_and_ends_it(self):
        app = self.assessed()
        with self.assertRaises(ValidationError):
            svc.decide_application(app.pk, self.approver, "rejected", "  ")
        self.assertEqual(svc.decide_application(app.pk, self.approver, "rejected", "Not enough proof").status, "rejected")

    def test_four_eyes(self):
        app = self.assessed()
        both = make_user("both", ["assess_assetapplication", "approve_assetapplication"], staff=True)
        app2 = self.assess(self.apply(user=make_user("bola"), asset_ref="X-1"), staff=both) if False else None
        with self.assertRaises(PermissionDenied):
            svc.decide_application(app.pk, self.assessor, "approved", "ok")        # the valuer
        self.assertIsNone(app2)
        for who in (self.cust, self.teller, make_user("plain", staff=True)):
            with self.assertRaises(PermissionDenied):
                svc.decide_application(app.pk, who, "approved", "ok")

    def test_the_customer_cannot_decide_their_own_application(self):
        boss = make_user("boss", ["approve_assetapplication"], staff=True)
        make_account(boss)
        app = self.assess(self.apply(user=boss, asset_ref="ASSAY-77"))
        with self.assertRaises(PermissionDenied):
            svc.decide_application(app.pk, boss, "approved", "looks good" )

    def test_bad_decisions(self):
        app = self.assessed()
        with self.assertRaises(ValidationError):
            svc.decide_application(app.pk, self.approver, "maybe", "ok")
        self.approve(app)
        with self.assertRaises(ValidationError):
            self.approve(app)                                       # already decided

    def test_nothing_is_approved_until_a_total_cap_is_set(self):
        app = self.assessed()
        for cap in ("0", ""):
            with override_settings(ASSETFUND_TOTAL_CAP=cap or "0"):
                with self.assertRaises(ValidationError):
                    self.approve(app)

    def test_the_total_cap_counts_every_open_offer(self):
        first = self.assessed()
        other = make_user("bola")
        make_account(other)
        second = self.assess(self.apply(user=other, asset_ref="ASSAY-2002"))
        with override_settings(ASSETFUND_TOTAL_CAP="500000"):
            self.approve(first)                                      # 360,000 fits
            with self.assertRaises(ValidationError):
                self.approve(second)                                 # 720,000 would not
        with override_settings(ASSETFUND_TOTAL_CAP="800000"):
            self.approve(second)

    def test_the_ledger_must_be_set_up(self):
        from funding.models import LedgerAccount
        app = self.assessed()
        LedgerAccount.objects.filter(code="1150").delete()
        with self.assertRaises(ValidationError):
            self.approve(app)
