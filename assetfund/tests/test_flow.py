from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings
from django.utils import timezone

from accounts.models import Account
from assetfund import services as svc
from assetfund.models import AssetApplication
from funding.models import FundingPeriod, LedgerAccount, LedgerPosting
from transactions.models import Transaction

from .helpers import CFG, D, AssetCase, journal, make_user


def approved(case):
    app = case.apply()
    case.assess(app)
    case.approve(app)
    return AssetApplication.objects.get(pk=app.pk)


@override_settings(**CFG)
class AcceptTests(AssetCase):
    def test_accepting_starts_the_project_and_sets_the_maturity_date(self):
        app = self.accept(approved(self))
        self.assertEqual(app.status, "active")
        self.assertEqual(app.maturity_date, timezone.localdate() + timedelta(days=180))

    def test_only_the_owner_can_accept(self):
        app = approved(self)
        with self.assertRaises(PermissionDenied):
            svc.accept_offer(make_user("thief"), app.pk, "1234")

    def test_a_wrong_pin_is_refused_and_the_attempt_is_kept(self):
        app = approved(self)
        with self.assertRaises(ValidationError):
            self.accept(app, pin="9999")
        self.assertEqual(AssetApplication.objects.get(pk=app.pk).status, "approved")
        self.assertEqual(Account.objects.get(pk=self.acc.pk).pin_attempts, 1)

    def test_three_wrong_pins_lock_the_account(self):
        app = approved(self)
        for _ in range(3):
            with self.assertRaises(ValidationError):
                self.accept(app, pin="9999")
        with self.assertRaises(ValidationError):
            self.accept(app, pin="1234")                              # right PIN, but locked now
        self.assertEqual(AssetApplication.objects.get(pk=app.pk).status, "approved")

    def test_a_missing_pin_or_no_pin_set_is_refused(self):
        app = approved(self)
        with self.assertRaises(ValidationError):
            self.accept(app, pin="")
        Account.objects.filter(pk=self.acc.pk).update(pin_hash="")
        with self.assertRaises(ValidationError):
            self.accept(app, pin="1234")

    def test_an_expired_offer_cannot_be_accepted_and_the_job_expires_it(self):
        app = approved(self)
        AssetApplication.objects.filter(pk=app.pk).update(offer_expires_at=timezone.now() - timedelta(minutes=1))
        with self.assertRaises(ValidationError):
            self.accept(app)
        self.assertEqual(svc.expire_offers(), 1)
        self.assertEqual(AssetApplication.objects.get(pk=app.pk).status, "expired")

    def test_a_closed_account_cannot_accept(self):
        app = approved(self)
        Account.objects.filter(pk=self.acc.pk).update(closed_at=timezone.now())
        with self.assertRaises(ValidationError):
            self.accept(app)


@override_settings(**CFG)
class ReleaseTests(AssetCase):
    def test_the_first_stage_needs_no_evidence_and_credits_the_account(self):
        app = self.activate()
        stage = self.release(app, 1)
        self.assertEqual(stage.status, "released")
        self.assertEqual(self.balance(), D("100000.00"))
        self.assertEqual(journal(f"JE-ASSET-{stage.pk}"), {("1150", "debit"): D("100000.00"), ("2300", "credit"): D("100000.00")})
        self.assertTrue(Transaction.objects.filter(account=self.acc, transaction_type="asset_advance", amount=D("100000.00")).exists())
        self.assertEqual(AssetApplication.objects.get(pk=app.pk).released_total, D("100000.00"))

    def test_later_stages_need_evidence_and_the_right_order(self):
        app = self.activate()
        stages = {s.order: s for s in app.stages.all()}
        with self.assertRaises(ValidationError):
            svc.release_stage(stages[2].pk, self.teller)               # stage 1 first
        with self.assertRaises(ValidationError):
            svc.submit_stage_evidence(app.user, stages[2].pk, "Moulds bought, photos kept")
        self.release(app, 1)
        with self.assertRaises(ValidationError):
            svc.release_stage(stages[2].pk, self.teller)               # no evidence yet
        with self.assertRaises(ValidationError):
            svc.submit_stage_evidence(app.user, stages[3].pk, "Too early for stage three")
        with self.assertRaises(ValidationError):
            svc.submit_stage_evidence(app.user, stages[2].pk, "short")
        self.release(app, 2)
        self.assertEqual(self.balance(), D("260000.00"))

    def test_the_first_stage_takes_no_evidence(self):
        app = self.activate()
        with self.assertRaises(ValidationError):
            svc.submit_stage_evidence(app.user, app.stages.get(order=1).pk, "Nothing to prove yet here")

    def test_only_the_owner_sends_evidence(self):
        app = self.activate()
        self.release(app, 1)
        with self.assertRaises(PermissionDenied):
            svc.submit_stage_evidence(make_user("thief"), app.stages.get(order=2).pk, "Not my project at all")

    def test_who_may_release(self):
        app = self.activate()
        stage = app.stages.get(order=1)
        for who in (self.cust, self.assessor, self.approver, make_user("plain", staff=True)):
            with self.assertRaises(PermissionDenied):                  # the valuer holds the permission but is barred
                svc.release_stage(stage.pk, who)
        self.assertEqual(self.balance(), D("0.00"))

    def test_a_stage_is_released_only_once(self):
        app = self.activate()
        stage = self.release(app, 1)
        with self.assertRaises(ValidationError):
            svc.release_stage(stage.pk, self.teller)
        self.assertEqual(self.balance(), D("100000.00"))

    def test_a_closed_period_or_missing_ledger_account_changes_nothing(self):
        app = self.activate()
        stage = app.stages.get(order=1)
        FundingPeriod.objects.update(status="closed")
        with self.assertRaises(ValidationError):
            svc.release_stage(stage.pk, self.teller)
        FundingPeriod.objects.update(status="open")
        LedgerAccount.objects.filter(code="1150").delete()
        with self.assertRaises(ValidationError):
            svc.release_stage(stage.pk, self.teller)
        self.assertEqual((self.balance(), LedgerPosting.objects.count()), (D("0.00"), 0))
        self.assertEqual(Transaction.objects.filter(transaction_type="asset_advance").count(), 0)

    def test_an_application_that_is_not_active_cannot_release(self):
        app = self.apply()
        self.assess(app)
        with self.assertRaises(ValidationError):
            svc.release_stage(app.stages.get(order=1).pk, self.teller)


@override_settings(**CFG)
class SaleTests(AssetCase):
    def started(self):
        app = self.activate()
        self.release(app, 1)                                           # 100,000 released, fee due 10,000
        return app

    def report(self, app, amount="200000", **kw):
        return svc.report_sale(app.user, app.pk, amount, "Sold twelve gold necklaces to a retailer", **kw)

    def test_a_sale_is_reported_as_pending_by_the_owner_only(self):
        app = self.started()
        sale = self.report(app)
        self.assertEqual((sale.status, sale.proceeds), ("pending", D("200000.00")))
        with self.assertRaises(PermissionDenied):
            svc.report_sale(make_user("thief"), app.pk, "1000", "Not my project at all")

    def test_bad_sales_are_refused_and_pending_sales_are_limited(self):
        app = self.started()
        for bad in ("abc", "0", "-5", "10.555", "100000001"):
            with self.assertRaises(ValidationError, msg=bad):
                self.report(app, bad)
        for _ in range(3):
            self.report(app, "1000")
        with self.assertRaises(ValidationError):
            self.report(app, "1000")

    def test_klosa_takes_its_share_from_the_customers_balance_against_principal_first(self):
        app = self.started()
        self.fund("200000")                                            # balance 300,000
        sale = svc.confirm_sale(self.report(app).pk, self.teller)
        self.assertEqual((sale.status, sale.share_due, sale.applied_principal, sale.applied_fee, sale.shortfall),
                         ("confirmed", D("100000.00"), D("100000.00"), D("0.00"), D("0.00")))
        self.assertEqual(self.balance(), D("200000.00"))
        self.assertEqual(journal(f"JE-ASSETR-{sale.pk}"), {("2300", "debit"): D("100000.00"), ("1150", "credit"): D("100000.00")})
        app = AssetApplication.objects.get(pk=app.pk)
        self.assertEqual((app.recovered_principal, app.status), (D("100000.00"), "active"))   # more stages to come
        self.assertTrue(Transaction.objects.filter(transaction_type="asset_repayment", amount=D("100000.00")).exists())

    def test_the_fee_is_taken_after_the_principal(self):
        app = self.started()
        self.fund("500000")
        svc.confirm_sale(self.report(app).pk, self.teller)             # clears the 100,000 principal
        sale = svc.confirm_sale(self.report(app, "40000").pk, self.teller)   # 50% = 20,000, only 10,000 fee is due
        self.assertEqual((sale.share_due, sale.applied_principal, sale.applied_fee), (D("10000.00"), D("0.00"), D("10000.00")))
        self.assertEqual(journal(f"JE-ASSETR-{sale.pk}"), {("2300", "debit"): D("10000.00"), ("4100", "credit"): D("10000.00")})

    def test_a_short_balance_leaves_a_shortfall_and_changes_nothing_else(self):
        app = self.started()                                           # balance is 100,000 from the release
        Account.objects.filter(pk=self.acc.pk).update(balance=D("0.00"))
        sale = svc.confirm_sale(self.report(app).pk, self.teller)
        self.assertEqual((sale.status, sale.shortfall, sale.applied_principal), ("confirmed", D("100000.00"), D("0.00")))
        self.assertEqual(AssetApplication.objects.get(pk=app.pk).recovered_principal, D("0.00"))
        self.assertEqual(LedgerPosting.objects.filter(source_type="asset_repay").count(), 0)

    def test_staff_can_confirm_a_lower_amount_than_reported(self):
        app = self.started()
        self.fund("200000")
        sale = svc.confirm_sale(self.report(app).pk, self.teller, verified_proceeds="100000")
        self.assertEqual((sale.confirmed_proceeds, sale.share_due), (D("100000.00"), D("50000.00")))

    def test_a_sale_is_confirmed_once_and_only_by_the_right_people(self):
        app = self.started()
        self.fund("200000")
        sale = self.report(app)
        for who in (self.cust, self.assessor, self.approver):
            with self.assertRaises(PermissionDenied):
                svc.confirm_sale(sale.pk, who)
        svc.confirm_sale(sale.pk, self.teller)
        with self.assertRaises(ValidationError):
            svc.confirm_sale(sale.pk, self.teller)

    def test_a_rejected_sale_moves_no_money(self):
        app = self.started()
        sale = self.report(app)
        with self.assertRaises(ValidationError):
            svc.reject_sale(sale.pk, self.teller, " ")
        self.assertEqual(svc.reject_sale(sale.pk, self.teller, "Buyer could not be found").status, "rejected")
        self.assertEqual(self.balance(), D("100000.00"))

    def test_the_final_sale_clears_the_debt_and_settles_the_project(self):
        app = self.activate()
        for order in (1, 2, 3):
            self.release(app, order)                                   # 360,000 released, fee 36,000
        self.fund("800000")
        sale = svc.confirm_sale(self.report(app, "800000", is_final=True).pk, self.teller)
        self.assertEqual((sale.applied_principal, sale.applied_fee), (D("360000.00"), D("36000.00")))
        self.assertEqual(AssetApplication.objects.get(pk=app.pk).status, "settled")
        self.assertEqual(self.balance(), D("764000.00"))               # 360,000 + 800,000 - 396,000

    def test_a_final_sale_that_falls_short_cancels_the_unreleased_stages_and_goes_overdue(self):
        app = self.started()
        self.fund("100000")
        svc.confirm_sale(self.report(app, "100000", is_final=True).pk, self.teller)
        app = AssetApplication.objects.get(pk=app.pk)
        self.assertEqual(app.status, "overdue")
        self.assertEqual(sorted(app.stages.values_list("status", flat=True)), ["cancelled", "cancelled", "released"])


@override_settings(**CFG)
class JobsAndExposureTests(AssetCase):
    def test_exposure_counts_offers_and_what_is_still_out_or_to_come(self):
        app = approved(self)
        self.assertEqual(svc.exposure(), D("360000.00"))
        self.accept(app)
        self.release(app, 1)
        self.assertEqual(svc.exposure(), D("360000.00"))               # 100,000 out + 260,000 still to come

    def test_the_overdue_job_marks_only_projects_that_owe_money(self):
        app = self.activate()
        AssetApplication.objects.filter(pk=app.pk).update(maturity_date=timezone.localdate() - timedelta(days=1))
        self.assertEqual(svc.mark_overdue(), 0)                        # nothing released, nothing owed
        self.release(app, 1)
        self.assertEqual(svc.mark_overdue(), 1)
        self.assertEqual(AssetApplication.objects.get(pk=app.pk).status, "overdue")
