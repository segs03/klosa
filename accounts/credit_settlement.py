import uuid
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from backoffice.audit import log as audit_log
from transactions.models import CreditTransaction
from .models import Account, CreditAccount, CreditSettlement


def _need(user, perm):
    if not (user and user.is_authenticated and user.is_active and user.is_staff and user.has_perm(perm)):
        raise PermissionDenied("You are not allowed to do this.")


def propose(staff, credit_pk, kind, amount, asset, evidence_ref):
    _need(staff, "accounts.propose_creditsettlement")
    if kind not in dict(CreditSettlement.KINDS):
        raise ValidationError("Choose the kind of settlement.")
    try:
        amount = Decimal(str(amount).strip())
        ok = amount.is_finite() and amount > 0 and amount == amount.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        ok = False
    if not ok:
        raise ValidationError("Enter an amount above zero with at most 2 decimal places.")
    asset, evidence_ref = " ".join((asset or "").split()), " ".join((evidence_ref or "").split())
    if len(asset) < 3 or len(evidence_ref) < 3:
        raise ValidationError("Describe the asset and give the evidence reference.")
    with transaction.atomic():
        credit = CreditAccount.objects.select_for_update().filter(pk=credit_pk).first()
        if credit is None:
            raise ValidationError("Credit facility not found.")
        if credit.account.user_id == staff.pk:
            raise PermissionDenied("You cannot settle your own credit.")
        if credit.status not in ("active", "suspended"):
            raise ValidationError("This facility is not open.")
        pending = sum((s.amount for s in credit.settlements.filter(status="pending")), Decimal("0"))
        if amount + pending > credit.outstanding_balance:
            raise ValidationError("That is more than the customer owes, counting settlements already waiting.")
        return CreditSettlement.objects.create(credit_account=credit, kind=kind, amount=amount, asset=asset[:200],
                                               evidence_ref=evidence_ref[:100], reference="KLS" + uuid.uuid4().hex[:12].upper(),
                                               proposed_by=staff)


def decide(staff, pk, approve, reason=""):
    _need(staff, "accounts.approve_creditsettlement")
    reason = " ".join((reason or "").split())
    if not approve and not reason:
        raise ValidationError("Give a reason for rejecting.")
    cid = CreditSettlement.objects.filter(pk=pk).values_list("credit_account_id", flat=True).first()
    if cid is None:
        raise ValidationError("Settlement not found.")
    with transaction.atomic():
        acc_id = CreditAccount.objects.filter(pk=cid).values_list("account_id", flat=True).first()
        Account.objects.select_for_update().get(pk=acc_id)                 # account first, then credit, then the row
        credit = CreditAccount.objects.select_for_update().get(pk=cid)
        s = CreditSettlement.objects.select_for_update().get(pk=pk)
        if s.status != "pending":
            raise ValidationError("This settlement was already decided.")
        if s.proposed_by_id == staff.pk:
            raise PermissionDenied("The person who recorded a settlement cannot approve it.")
        s.decided_by, s.decided_at, s.reason = staff, timezone.now(), reason[:200]
        if not approve:
            s.status = "rejected"
        else:
            if s.amount > credit.outstanding_balance:
                raise ValidationError("The customer now owes less than this settlement.")
            credit.outstanding_balance -= s.amount
            credit.save(update_fields=["outstanding_balance"])
            CreditTransaction.objects.create(
                credit_account=credit, transaction_type="repayment", amount=s.amount, reference=s.reference,
                description=f"Settled by {s.get_kind_display().lower()}: {s.asset}"[:255])
            s.status = "approved"
        s.save(update_fields=["status", "decided_by", "decided_at", "reason"])
        audit_log(staff, "settlement_" + s.status, s.reference, f"{s.amount} {s.asset}")
    return s