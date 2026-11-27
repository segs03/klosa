"""Asset-backed advances.

Flow: apply -> assess (staff A) -> approve (staff B) -> customer accepts -> staged releases ->
customer reports sales -> staff confirm -> Klosa takes its share until advance + fee are recovered.

Money rules, all inside one transaction, locking the account first and then the application:
  * the ledger entry is posted FIRST, so a missing ledger account or closed period changes nothing;
  * balance changes always come with a Transaction row (asset_advance / asset_repayment);
  * every movement is journalled once (LedgerPosting is unique per source).
"""
import logging
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.models import Account
from backoffice.audit import log as audit_log
from funding.models import FundingPeriod, JournalEntry, JournalLine, LedgerAccount, LedgerPosting
from transactions.models import Transaction

from .models import CENT, LIVE, ZERO, AdvanceStage, AssetApplication, SaleRecord, money, norm_ref

log = logging.getLogger(__name__)
HUNDRED = Decimal("100")

RECEIVABLE, DEPOSITS, FEE_INCOME = "1150", "2300", "4100"          # add 1150 and 4100 to the ledger chart
RATE_CAPS = {"gold": 60, "jewelry": 55, "property": 50, "equipment": 45, "materials": 45,
             "skill_contract": 30, "other": 30}                    # the most Klosa advances against verified value, in %
MAX_STAGES, MIN_EVIDENCE, MAX_PENDING_SALES = 6, 10, 3
DUPLICATE = "This asset is already pledged or under review."


# ------------------------------------------------------------------ settings
def _cfg(name, default):
    return getattr(settings, name, default)


def total_cap():
    """Total Klosa may have out at once. 0 means asset funding is switched off."""
    return Decimal(str(_cfg("ASSETFUND_TOTAL_CAP", "0")))


def customer_cap():
    return Decimal(str(_cfg("ASSETFUND_CUSTOMER_CAP", "2000000")))


def min_kyc_tier():
    return int(_cfg("ASSETFUND_MIN_KYC_TIER", 2))                  # kyc_tier 2 is Tier 3 on the upgrade page


def max_fee_pct():
    return Decimal(str(_cfg("ASSETFUND_MAX_FEE_PCT", "15")))


def offer_days():
    return int(_cfg("ASSETFUND_OFFER_DAYS", 7))


# ------------------------------------------------------------------- helpers
def _need(user, perm):
    if not (user and user.is_authenticated and user.is_active and user.is_staff and user.has_perm(perm)):
        raise PermissionDenied("You are not allowed to do this.")


def _dec(value, label="The amount"):
    try:
        d = Decimal(str(value).strip())
        ok = d.is_finite() and d == d.quantize(CENT)
    except (InvalidOperation, ValueError):
        ok = False
    if not ok:
        raise ValidationError(f"{label} must be a number with at most 2 decimal places.")
    return d.quantize(CENT)


def _text(value, label, lo, hi):
    text = " ".join(str(value or "").split())
    if not lo <= len(text) <= hi:
        raise ValidationError(f"{label} must be between {lo} and {hi} characters.")
    return text


def _audit(user, action, app, detail=""):
    audit_log(user, action, f"asset:{app.pk}", str(detail)[:200])


def _lock(app_pk):
    """The account first, then the application, the same order the rest of Klosa uses."""
    user_id = AssetApplication.objects.filter(pk=app_pk).values_list("user_id", flat=True).first()
    if user_id is None:
        raise ValidationError("Application not found.")
    acc = Account.objects.select_for_update().get(user_id=user_id)
    app = AssetApplication.objects.select_for_update().get(pk=app_pk)
    return acc, app


def _open_period():
    period = FundingPeriod.objects.filter(start_date__lte=timezone.localdate(),
                                          end_date__gte=timezone.localdate(), status="open").first()
    if period is None:
        raise ValidationError("No open funding period covers today. Ask finance to open one.")
    return period


def _post(source_type, source_id, reference, description, lines):
    """Journal a movement once. lines = [(ledger code, 'debit' | 'credit', amount)]."""
    period = _open_period()
    codes = {c for c, _, _ in lines}
    accounts = {a.code: a for a in LedgerAccount.objects.filter(code__in=codes, is_active=True)}
    if codes - set(accounts):
        raise ValidationError(f"Ledger account {', '.join(sorted(codes - set(accounts)))} is not set up. Run seed_ledger.")
    posting, created = LedgerPosting.objects.get_or_create(source_type=source_type, source_id=source_id)
    if not created:
        raise ValidationError("This movement was already posted.")
    entry = JournalEntry.objects.create(reference=reference, period=period, description=description[:255])
    debit = credit = ZERO
    for code, side, amount in lines:
        if amount <= ZERO:
            continue
        JournalLine.objects.create(entry=entry, account=accounts[code], description=reference, **{side: amount})
        debit, credit = (debit + amount, credit) if side == "debit" else (debit, credit + amount)
    if debit != credit or debit <= ZERO:
        raise ValidationError("Journal entry is not balanced.")
    entry.status, entry.posted_at = "posted", timezone.now()
    entry.save()
    posting.entry = entry
    posting.save()
    return entry


def exposure(exclude_pk=None):
    """What Klosa has out or has promised: approved offers in full, plus live advances' unrecovered
    principal and their stages still to come."""
    rows = AssetApplication.objects.all()
    if exclude_pk:
        rows = rows.exclude(pk=exclude_pk)
    approved = rows.filter(status="approved").aggregate(a=Sum("advance_amount"))["a"] or ZERO
    live = rows.filter(status__in=("active", "overdue"))
    out = live.aggregate(r=Sum("released_total"), p=Sum("recovered_principal"))
    coming = (AdvanceStage.objects.filter(application__in=live, status__in=("planned", "requested"))
              .aggregate(a=Sum("amount"))["a"] or ZERO)
    return approved + (out["r"] or ZERO) - (out["p"] or ZERO) + coming


def offer_summary(app):
    """Plain words for the customer, shown before they accept."""
    total = app.advance_amount + app.fee_amount
    stages = app.stages.order_by("order")
    return [
        f"You receive ₦{app.advance_amount:,.2f}, in {stages.count()} stage(s).",
        f"The fee is ₦{app.fee_amount:,.2f}. It grows as each stage is released.",
        f"When you sell, Klosa takes {app.share_pct}% of each sale until ₦{total:,.2f} is recovered. The rest is yours.",
        f"You have {app.term_days} days. If the sales have not covered it by then, Klosa will review it with you, "
        "and the pledged asset stays as security until then.",
    ] + [f"Stage {s.order}: ₦{s.amount:,.2f} for {s.title}." for s in stages]


# ----------------------------------------------------------------- customer
def submit_application(user, *, kind, title, description, asset_ref, declared_value, requested_amount, purpose):
    if kind not in dict(AssetApplication.KINDS):
        raise ValidationError("Choose what kind of asset this is.")
    title = _text(title, "The title", 3, 120)
    description = _text(description, "The description", 20, 1000)
    purpose = _text(purpose, "What the money is for", 10, 300)
    asset_ref = _text(asset_ref, "The asset reference", 3, 80)
    if len(norm_ref(asset_ref)) < 3:
        raise ValidationError("The asset reference needs at least 3 letters or numbers.")
    declared, requested = _dec(declared_value, "The value"), _dec(requested_amount, "The amount")
    if declared <= ZERO or requested <= ZERO:
        raise ValidationError("Enter amounts above zero.")
    if requested > declared:
        raise ValidationError("You cannot ask for more than the value you declared.")
    if requested > customer_cap():
        raise ValidationError(f"The most one customer can ask for is ₦{customer_cap():,.0f}.")
    with transaction.atomic():
        acc = Account.objects.select_for_update().filter(user=user).first()
        if acc is None or acc.closed_at:
            raise ValidationError("Your account is not open.")
        if acc.kyc_tier < min_kyc_tier():
            raise ValidationError(f"Complete your verification to Tier {min_kyc_tier() + 1} first.")
        if AssetApplication.objects.filter(user=user, status__in=LIVE).exists():
            raise ValidationError("You already have an application in progress.")
        if AssetApplication.objects.filter(kind=kind, asset_ref_norm=norm_ref(asset_ref), status__in=LIVE).exists():
            raise ValidationError(DUPLICATE)
        try:
            with transaction.atomic():
                app = AssetApplication.objects.create(
                    user=user, kind=kind, title=title, description=description, asset_ref=asset_ref,
                    declared_value=declared, requested_amount=requested, purpose=purpose)
        except IntegrityError:                                       # two people pledged it at the same moment
            raise ValidationError(DUPLICATE)
    _audit(user, "asset.submitted", app, f"{kind} ₦{requested}")
    return app


def withdraw_application(user, app_pk):
    with transaction.atomic():
        app = AssetApplication.objects.select_for_update().get(pk=app_pk)
        if app.user_id != user.pk:
            raise PermissionDenied("This is not your application.")
        if app.status not in ("pending", "assessed", "approved"):
            raise ValidationError("This application can no longer be withdrawn.")
        app.status = "withdrawn"
        app.save(update_fields=["status"])
    _audit(user, "asset.withdrawn", app)
    return app


def _face_ok(user, app):
    """Optional face check: only demanded when 'advance' is listed in FACE_ENFORCE."""
    from face_auth import service as face
    return not face.required(user, "advance") or face.consume(user, "advance", str(app.pk))


def accept_offer(user, app_pk, pin):
    bad = None
    with transaction.atomic():
        acc, app = _lock(app_pk)
        if app.user_id != user.pk:
            raise PermissionDenied("This is not your offer.")
        if app.status != "approved":
            raise ValidationError("This offer is not open.")
        if app.offer_expires_at <= timezone.now():
            raise ValidationError("This offer has expired.")
        if acc.closed_at:
            raise ValidationError("Your account is closed.")
        if acc.is_locked():
            raise ValidationError("Your account is temporarily locked.")
        if not acc.pin_hash:
            raise ValidationError("Please set your transaction PIN first.")
        if not str(pin or "").strip():
            raise ValidationError("Enter your transaction PIN.")
        if not acc.check_pin(str(pin)):                              # saved on the locked row, so the lock counts
            bad = "Your account is temporarily locked." if acc.is_locked() else "Incorrect transaction PIN."
        else:
            if not _face_ok(user, app):
                raise ValidationError("Complete the face check first.")
            now = timezone.now()
            app.status, app.accepted_at = "active", now
            app.maturity_date = timezone.localdate(now) + timedelta(days=app.term_days)
            app.save(update_fields=["status", "accepted_at", "maturity_date"])
    if bad:
        raise ValidationError(bad)                                   # after the commit, so the attempt is kept
    _audit(user, "asset.accepted", app)
    return app


def submit_stage_evidence(user, stage_pk, note):
    note = _text(note, "The progress note", MIN_EVIDENCE, 500)
    with transaction.atomic():
        stage = AdvanceStage.objects.select_for_update().select_related("application").get(pk=stage_pk)
        app = stage.application
        if app.user_id != user.pk:
            raise PermissionDenied("This is not your project.")
        if app.status != "active":
            raise ValidationError("This project is not in progress.")
        if stage.order == 1:
            raise ValidationError("The first stage needs no evidence.")
        if stage.status != "planned" or stage.order != _next_order(app):
            raise ValidationError("Finish the earlier stage first.")
        stage.status, stage.evidence_note, stage.requested_at = "requested", note, timezone.now()
        stage.save(update_fields=["status", "evidence_note", "requested_at"])
    _audit(user, "asset.evidence", app, f"stage {stage.order}")
    return stage


def report_sale(user, app_pk, proceeds, note, is_final=False):
    proceeds = _dec(proceeds, "The sale amount")
    if proceeds <= ZERO or proceeds > Decimal("100000000"):
        raise ValidationError("Enter a sale amount above zero.")
    note = _text(note, "The sale note", 10, 300)
    with transaction.atomic():
        app = AssetApplication.objects.select_for_update().get(pk=app_pk)
        if app.user_id != user.pk:
            raise PermissionDenied("This is not your project.")
        if app.status not in ("active", "overdue"):
            raise ValidationError("This project is not in progress.")
        if app.sales.filter(status="pending").count() >= MAX_PENDING_SALES:
            raise ValidationError("Wait for your earlier sales to be confirmed first.")
        sale = SaleRecord.objects.create(application=app, reference="SL" + uuid.uuid4().hex[:10].upper(),
                                         proceeds=proceeds, note=note, is_final=bool(is_final))
    _audit(user, "asset.sale_reported", app, f"₦{proceeds}")
    return sale


# --------------------------------------------------------------------- staff
def assess_application(app_pk, assessor, *, verified_value, advance_rate, fee_pct, share_pct, term_days, stages, note):
    _need(assessor, "assetfund.assess_assetapplication")
    note = _text(note, "The assessment note", 10, 500)
    verified = _dec(verified_value, "The verified value")
    rate, fee_rate, share = _dec(advance_rate, "The advance rate"), _dec(fee_pct, "The fee"), _dec(share_pct, "The share")
    with transaction.atomic():
        app = AssetApplication.objects.select_for_update().get(pk=app_pk)
        if app.status != "pending":
            raise ValidationError("This application is not waiting for review.")
        if app.user_id == assessor.pk:
            raise PermissionDenied("You cannot assess your own application.")
        if verified <= ZERO:
            raise ValidationError("The verified value must be above zero.")
        cap = Decimal(RATE_CAPS[app.kind])
        if not ZERO < rate <= cap:
            raise ValidationError(f"For this kind of asset the advance rate can be at most {cap}%.")
        if not ZERO <= fee_rate <= max_fee_pct():
            raise ValidationError(f"The fee can be at most {max_fee_pct()}%.")
        if not Decimal("1") <= share <= HUNDRED:
            raise ValidationError("Klosa's share of each sale must be between 1% and 100%.")
        if not isinstance(term_days, int) or not 30 <= term_days <= 730:
            raise ValidationError("The term must be between 30 and 730 days.")
        advance = money(verified * rate / HUNDRED)
        if advance <= ZERO:
            raise ValidationError("The advance would be zero.")
        if advance > app.requested_amount:
            raise ValidationError(f"The advance (₦{advance:,.2f}) is more than the customer asked for "
                                  f"(₦{app.requested_amount:,.2f}). Lower the rate.")
        if advance > customer_cap():
            raise ValidationError(f"The most one customer can receive is ₦{customer_cap():,.0f}.")
        plan = [(_text(t, "A stage title", 3, 120), _dec(a, "A stage amount")) for t, a in (stages or [])]
        if not 1 <= len(plan) <= MAX_STAGES:
            raise ValidationError(f"Plan between 1 and {MAX_STAGES} stages.")
        if any(a <= ZERO for _, a in plan):
            raise ValidationError("Every stage needs an amount above zero.")
        planned = sum((a for _, a in plan), ZERO)
        if planned != advance:
            raise ValidationError(f"The stages add up to ₦{planned:,.2f} but the advance is ₦{advance:,.2f}.")
        app.verified_value, app.advance_rate, app.advance_amount = verified, rate, advance
        app.fee_pct, app.fee_amount, app.share_pct, app.term_days = fee_rate, money(advance * fee_rate / HUNDRED), share, term_days
        app.assessment_note, app.assessed_by, app.assessed_at, app.status = note, assessor, timezone.now(), "assessed"
        app.save()
        for order, (title, amount) in enumerate(plan, start=1):
            AdvanceStage.objects.create(application=app, order=order, title=title, amount=amount)
    _audit(assessor, "asset.assessed", app, f"value {verified} advance {advance}")
    return app


def decide_application(app_pk, approver, decision, reason):
    _need(approver, "assetfund.approve_assetapplication")
    reason = _text(reason, "The reason", 3, 500)
    if decision not in ("approved", "rejected"):
        raise ValidationError("Decision must be approved or rejected.")
    with transaction.atomic():
        app = AssetApplication.objects.select_for_update().get(pk=app_pk)
        if app.status != "assessed":
            raise ValidationError("This application is not waiting for a decision.")
        if approver.pk in (app.assessed_by_id, app.user_id):
            raise PermissionDenied("The person who valued it, or the customer, cannot decide it.")
        if decision == "approved":
            cap = total_cap()
            if cap <= ZERO:
                raise ValidationError("Asset funding is not open yet: no total cap is set.")
            # One shared row is locked so two approvals cannot both squeeze under the cap.
            if LedgerAccount.objects.select_for_update().filter(code=RECEIVABLE).first() is None:
                raise ValidationError(f"Ledger account {RECEIVABLE} is not set up. Run seed_ledger.")
            if exposure(exclude_pk=app.pk) + app.advance_amount > cap:
                raise ValidationError(f"This would take total exposure above the cap of ₦{cap:,.0f}.")
            app.offer_expires_at = timezone.now() + timedelta(days=offer_days())
        app.status = decision
        app.approved_by, app.decision_reason, app.decided_at = approver, reason, timezone.now()
        app.save()
    _audit(approver, f"asset.{decision}", app, reason)
    return app


def _next_order(app):
    stage = app.stages.filter(status__in=("planned", "requested")).order_by("order").first()
    return stage.order if stage else None


def release_stage(stage_pk, staff):
    _need(staff, "assetfund.release_advancestage")
    app_pk = AdvanceStage.objects.filter(pk=stage_pk).values_list("application_id", flat=True).first()
    if app_pk is None:
        raise ValidationError("Stage not found.")
    with transaction.atomic():
        acc, app = _lock(app_pk)
        stage = AdvanceStage.objects.select_for_update().get(pk=stage_pk)
        if staff.pk in (app.user_id, app.assessed_by_id):
            raise PermissionDenied("The customer, or the person who valued the asset, cannot release the money.")
        if app.status != "active":
            raise ValidationError("This project is not in progress.")
        if acc.closed_at:
            raise ValidationError("The customer's account is closed.")
        if stage.status == "released":
            raise ValidationError("This stage was already released.")
        if stage.status == "cancelled":
            raise ValidationError("This stage was cancelled.")
        if stage.order != _next_order(app):
            raise ValidationError("Release the earlier stage first.")
        if stage.order > 1 and stage.status != "requested":
            raise ValidationError("The customer has not sent evidence for this stage yet.")
        amount = stage.amount
        if app.released_total + amount > app.advance_amount:
            raise ValidationError("This would release more than the advance.")
        _post("asset_stage", stage.pk, f"JE-ASSET-{stage.pk}", f"Asset advance stage {stage.order} · {app.title}",
              [(RECEIVABLE, "debit", amount), (DEPOSITS, "credit", amount)])
        acc.balance += amount
        acc.save(update_fields=["balance"])
        Transaction.objects.create(account=acc, transaction_type="asset_advance", amount=amount,
                                   reference=f"ASA{stage.pk}", description=f"Asset advance, stage {stage.order}: {stage.title}"[:255])
        stage.status, stage.released_by, stage.released_at = "released", staff, timezone.now()
        stage.save(update_fields=["status", "released_by", "released_at"])
        app.released_total += amount
        app.save(update_fields=["released_total"])
    _audit(staff, "asset.stage_released", app, f"stage {stage.order} ₦{amount}")
    return stage


def confirm_sale(sale_pk, staff, verified_proceeds=None):
    _need(staff, "assetfund.confirm_assetsale")
    app_pk = SaleRecord.objects.filter(pk=sale_pk).values_list("application_id", flat=True).first()
    if app_pk is None:
        raise ValidationError("Sale not found.")
    with transaction.atomic():
        acc, app = _lock(app_pk)
        sale = SaleRecord.objects.select_for_update().get(pk=sale_pk)
        if sale.status != "pending":
            raise ValidationError("This sale was already handled.")
        if staff.pk == app.user_id:
            raise PermissionDenied("You cannot confirm your own sale.")
        if app.status not in ("active", "overdue"):
            raise ValidationError("This project is not in progress.")
        proceeds = sale.proceeds if verified_proceeds is None else _dec(verified_proceeds, "The verified sale amount")
        if proceeds <= ZERO:
            raise ValidationError("The verified sale amount must be above zero.")
        owed = app.outstanding
        share = min(money(proceeds * app.share_pct / HUNDRED), owed)
        pay = min(share, acc.balance)
        to_principal = min(pay, app.principal_outstanding)
        to_fee = pay - to_principal
        if pay > ZERO:
            _post("asset_repay", sale.pk, f"JE-ASSETR-{sale.pk}", f"Asset advance repayment · {app.title}",
                  [(DEPOSITS, "debit", pay), (RECEIVABLE, "credit", to_principal), (FEE_INCOME, "credit", to_fee)])
            acc.balance -= pay
            acc.save(update_fields=["balance"])
            Transaction.objects.create(account=acc, transaction_type="asset_repayment", amount=pay,
                                       reference=f"ASR{sale.pk}", description=f"Asset advance repayment · {app.title}"[:255])
            app.recovered_principal += to_principal
            app.recovered_fee += to_fee
        sale.status, sale.confirmed_proceeds, sale.share_due = "confirmed", proceeds, share
        sale.applied_principal, sale.applied_fee, sale.shortfall = to_principal, to_fee, share - pay
        sale.decided_by, sale.decided_at = staff, timezone.now()
        sale.save()
        remaining = owed - pay
        if sale.is_final:
            app.stages.filter(status__in=("planned", "requested")).update(status="cancelled")
            app.status = "settled" if remaining == ZERO else "overdue"
        elif remaining == ZERO and not app.stages.filter(status__in=("planned", "requested")).exists():
            app.status = "settled"
        app.save(update_fields=["recovered_principal", "recovered_fee", "status"])
    _audit(staff, "asset.sale_confirmed", app, f"₦{proceeds} took ₦{pay}")
    return sale


def reject_sale(sale_pk, staff, reason):
    _need(staff, "assetfund.confirm_assetsale")
    reason = _text(reason, "The reason", 3, 300)
    with transaction.atomic():
        sale = SaleRecord.objects.select_for_update().select_related("application").get(pk=sale_pk)
        if sale.status != "pending":
            raise ValidationError("This sale was already handled.")
        if staff.pk == sale.application.user_id:
            raise PermissionDenied("You cannot decide your own sale.")
        sale.status, sale.reason, sale.decided_by, sale.decided_at = "rejected", reason, staff, timezone.now()
        sale.save()
    _audit(staff, "asset.sale_rejected", sale.application, reason)
    return sale


# ---------------------------------------------------------- scheduled jobs
def expire_offers():
    return AssetApplication.objects.filter(status="approved", offer_expires_at__lte=timezone.now()).update(status="expired")


def mark_overdue():
    count = 0
    due = AssetApplication.objects.filter(status="active", maturity_date__lt=timezone.localdate())
    for app in due:
        if app.outstanding > ZERO:
            AssetApplication.objects.filter(pk=app.pk, status="active").update(status="overdue")
            count += 1
    return count
