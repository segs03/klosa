from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.credit_services import (_get_credit_policy, assess_credit_application, credit_summary,                                 decide_credit_application, eligibility_problem)

from accounts.models import (Account, CreditAccount, CreditApplication, KYCApplication, VerifiedIdentity, FaceCheck)

from accounts.utils import normalize_phone
from biller.models import BillPayment
from cards.models import BankCard, CardOrder
from cards.services import approve_order, issue_order, reject_order
from cashier.models import Deposit, Withdrawal
from savings.models import SavingsPlan
from transactions.models import Transaction
from treasury.models import CashRequest
from treasury.services import TreasuryError, approve_cash_in, reject_cash_in
from .audit import log
from .models import AuditLog

from funding.models import ContributionIntent, CapitalContribution




User = get_user_model()


def need(user, *perms):
    if not (user.is_active and user.is_staff):
        raise PermissionDenied
    if perms and not any(user.has_perm(p) for p in perms):
        raise PermissionDenied


def _problem(e):
    if isinstance(e, ValidationError):
        return e.messages[0]
    return str(e) or "Not allowed."


@login_required
def home(request):
    u = request.user
    need(u)                                                 # staff only; the tiles depend on permissions
    has = u.has_perm
    tiles = []
    if has("accounts.review_kycapplication"):
        tiles.append(("ID reviews", KYCApplication.objects.filter(status__in=("pending", "under_review")).count(),
                      "accounts:kyc_staff", "fa-id-card"))
    if has("cards.approve_cardorder"):
        tiles.append(("Card requests", CardOrder.objects.filter(status__in=("pending", "approved", "failed")).count(),
                      "backoffice:cards", "fa-credit-card"))
    credit = 0
    if has("accounts.assess_creditapplication"):
        credit += CreditApplication.objects.filter(status="pending").count()
    if has("accounts.approve_creditapplication"):
        credit += CreditApplication.objects.filter(status="assessed").count()
    if has("accounts.assess_creditapplication") or has("accounts.approve_creditapplication"):
        tiles.append(("Credit applications", credit, "backoffice:credit", "fa-hand-holding-dollar"))
    if has("treasury.approve_cash"):
        tiles.append(("Cash deposits", CashRequest.objects.filter(status="pending").count(),
                      "backoffice:cash", "fa-money-bill-wave"))
    if has("treasury.record_cash"):
        tiles.append(("Record cash in", None, "treasury:cash_in", "fa-plus"))
    if has("treasury.pay_cashout"):
        tiles.append(("Pay out cash", None, "treasury:pay", "fa-arrow-up-from-bracket"))
    if has("backoffice.view_customers"):
        tiles.append(("Customers", None, "backoffice:customers", "fa-users"))
    if has("backoffice.view_operations"):
        cutoff = timezone.now() - timedelta(minutes=30)
        n = (BillPayment.objects.filter(status="processing", created_at__lt=cutoff).count()
             + Withdrawal.objects.filter(status="processing", created_at__lt=cutoff).count()
             + Deposit.objects.filter(status="review").count())
        tiles.append(("Needs attention", n, "backoffice:ops", "fa-triangle-exclamation"))
    if has("backoffice.view_audit"):
        tiles.append(("Audit log", None, "backoffice:audit", "fa-clipboard-list"))
    
    if has("accounts.review_facecheck"):
        tiles.append(("Face checks", FaceCheck.objects.filter(status="review").count(), "accounts:face_queue", "fa-face-smile"))
        
    if has("funding.confirm_contributionintent"):
        tiles.append(("Deposit notices", ContributionIntent.objects.filter(status="pending").count(), "funding:intents", "fa-coins"))
        
    if has("funding.verify_capitalcontribution"):
        tiles.append(("Verify contributions", CapitalContribution.objects.filter(source_type="investor", verified=False).count(), "funding:contributions", "fa-scale-balanced"))
    
    return render(request, "backoffice/home.html", {"tiles": tiles})


# ------------------------------------------------------------------ cards
@login_required
def cards_queue(request):
    need(request.user, "cards.approve_cardorder")
    orders = (CardOrder.objects.filter(status__in=("pending", "approved", "failed"))
              .select_related("account__user").order_by("created_at")[:100])
    return render(request, "backoffice/cards.html", {"orders": orders})


@login_required
def card_detail(request, pk):
    need(request.user, "cards.approve_cardorder")
    order = get_object_or_404(CardOrder.objects.select_related("account__user"), pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "approve":
                approve_order(order.pk, request.user)
            elif action == "reject":
                reject_order(order.pk, request.user, request.POST.get("reason", ""))
            elif action == "issue":
                issue_order(order.pk)
            else:
                raise ValidationError("Choose an action.")
        except (ValidationError, PermissionDenied) as e:
            messages.error(request, _problem(e))
        else:
            log(request.user, f"card_{action}", f"cardorder:{order.pk}", request.POST.get("reason", ""))
            messages.success(request, "Done.")
        return redirect("backoffice:card_detail", pk=order.pk)
    user = order.account.user
    return render(request, "backoffice/card_detail.html", {
        "order": order, "own": user.pk == request.user.pk,
        "verified": VerifiedIdentity.objects.filter(user=user).exists(),
        "credit": credit_summary(order.account)})


# ----------------------------------------------------------------- credit
@login_required
def credit_queue(request):
    u = request.user
    need(u, "accounts.assess_creditapplication", "accounts.approve_creditapplication")
    qs = CreditApplication.objects.select_related("user").order_by("submitted_at")
    return render(request, "backoffice/credit.html", {
        "to_assess": qs.filter(status="pending") if u.has_perm("accounts.assess_creditapplication") else [],
        "to_decide": qs.filter(status="assessed") if u.has_perm("accounts.approve_creditapplication") else []})


@login_required
def credit_detail(request, pk):
    u = request.user
    need(u, "accounts.assess_creditapplication", "accounts.approve_creditapplication")
    app = get_object_or_404(CreditApplication.objects.select_related("user"), pk=pk)
    account = Account.objects.filter(user=app.user).first()
    if request.method == "POST":
        action = request.POST.get("action")
        reason = request.POST.get("reason", "")
        try:
            if action == "assess":
                assess_credit_application(application_id=app.pk, assessor=u,
                                          assessed_amount=request.POST.get("amount", ""), reason=reason)
            elif action in ("approved", "rejected"):
                decide_credit_application(application_id=app.pk, approver=u, decision=action, reason=reason)
            else:
                raise ValidationError("Choose an action.")
        except (ValidationError, PermissionDenied) as e:
            messages.error(request, _problem(e))
        else:
            log(u, f"credit_{action}", f"creditapp:{app.pk}", reason)
            messages.success(request, "Saved.")
        return redirect("backoffice:credit_detail", pk=app.pk)
    try:
        policy = _get_credit_policy(account) if account else None
    except ValidationError:
        policy = None
    return render(request, "backoffice/credit_detail.html", {
        "app": app, "account": account, "policy": policy,
        "problem": eligibility_problem(account) if account else "This customer has no account.",
        "age_days": (timezone.now() - account.created_at).days if account else 0,
        "can_assess": u.has_perm("accounts.assess_creditapplication") and app.status == "pending",
        "can_decide": u.has_perm("accounts.approve_creditapplication") and app.status == "assessed"})


# ------------------------------------------------------------------- cash
@login_required
def cash_queue(request):
    need(request.user, "treasury.approve_cash")
    reqs = CashRequest.objects.filter(status="pending").select_related("account__user", "maker").order_by("created_at")
    return render(request, "backoffice/cash.html", {"reqs": reqs})


@login_required
@require_POST
def cash_decide(request, pk):
    need(request.user, "treasury.approve_cash")
    req = get_object_or_404(CashRequest, pk=pk)
    action = request.POST.get("action")
    try:
        if action == "approve":
            approve_cash_in(req.pk, request.user)
        elif action == "reject":
            reject_cash_in(req.pk, request.user, request.POST.get("reason") or "Rejected by approver")
        else:
            raise TreasuryError("Choose an action.")
    except (TreasuryError, PermissionDenied) as e:
        messages.error(request, _problem(e))
    else:
        log(request.user, f"cash_{action}", f"cashin:{req.reference}", request.POST.get("reason", ""))
        messages.success(request, "Done.")
    return redirect("backoffice:cash")


# -------------------------------------------------------------- customers
@login_required
def customers(request):
    need(request.user, "backoffice.view_customers")
    q = (request.GET.get("q") or "").strip()[:60]
    qs = User.objects.filter(is_staff=False).select_related("account")
    if q:
        cond = (Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
                | Q(account__account_number=q))
        phone = normalize_phone(q)
        if phone:
            cond |= Q(phone_number=phone)
        qs = qs.filter(cond)
    page = Paginator(qs.order_by("-date_joined"), 25).get_page(request.GET.get("page"))
    return render(request, "backoffice/customers.html", {"page": page, "q": q})


@login_required
def customer_detail(request, pk):
    need(request.user, "backoffice.view_customers")
    cust = get_object_or_404(User.objects.select_related("account"), pk=pk, is_staff=False)
    account = getattr(cust, "account", None)
    can_manage = request.user.has_perm("backoffice.manage_customers")
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied
        action = request.POST.get("action")
        reason = " ".join(request.POST.get("reason", "").split())[:200]
        if not reason:
            messages.error(request, "Give a reason.")
        elif action == "unlock" and account:
            Account.objects.filter(pk=account.pk).update(pin_attempts=0, locked_until=None)
            log(request.user, "unlock_pin", f"user:{cust.pk}", reason)
            messages.success(request, "PIN lock cleared.")
        elif action == "suspend":
            cust.is_active = False
            cust.save(update_fields=["is_active"])
            log(request.user, "suspend_login", f"user:{cust.pk}", reason)
            messages.success(request, "Login suspended. Their sessions end now.")
        elif action == "reactivate":
            if account and account.closed_at:
                messages.error(request, "A closed account cannot be reactivated.")
            else:
                cust.is_active = True
                cust.save(update_fields=["is_active"])
                log(request.user, "reactivate_login", f"user:{cust.pk}", reason)
                messages.success(request, "Login restored.")
        else:
            messages.error(request, "Choose an action.")
        return redirect("backoffice:customer", pk=cust.pk)
    ctx = {"cust": cust, "account": account, "can_manage": can_manage,
           "ident": VerifiedIdentity.objects.filter(user=cust).exists(),
           "kyc": KYCApplication.objects.filter(user=cust).first(),
           "history": AuditLog.objects.filter(target=f"user:{cust.pk}")[:10]}
    if account:
        ctx.update({
            "txs": Transaction.objects.filter(account=account)[:20],
            "cards": BankCard.objects.filter(account=account),
            "credit": CreditAccount.objects.filter(account=account).first(),
            "saved": SavingsPlan.objects.filter(account=account, status="active").aggregate(t=Sum("balance"))["t"] or 0})
    return render(request, "backoffice/customer.html", ctx)


# ------------------------------------------------------- stuck items, audit
@login_required
def operations(request):
    need(request.user, "backoffice.view_operations")
    cutoff = timezone.now() - timedelta(minutes=30)
    return render(request, "backoffice/ops.html", {
        "bills": BillPayment.objects.filter(status="processing", created_at__lt=cutoff).select_related("account__user")[:50],
        "withdrawals": Withdrawal.objects.filter(status="processing", created_at__lt=cutoff).select_related("account__user")[:50],
        "deposits": Deposit.objects.filter(status="review").select_related("account__user")[:50],
        "failed_cards": CardOrder.objects.filter(status="failed").select_related("account__user")[:50]})


@login_required
def audit(request):
    need(request.user, "backoffice.view_audit")
    return render(request, "backoffice/audit.html", {"entries": AuditLog.objects.select_related("staff")[:200]})