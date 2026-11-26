
import uuid
from django.contrib.auth.decorators import login_required

from .models import Account, CreditAccount, CreditApplication
from transactions.models import Transaction, CreditTransaction

from beneficiary.models import Beneficiary

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout

from django.db.models import Sum, Q

from django.utils import timezone
from django.db.models.functions import Coalesce

from django.contrib import messages
from django.views.decorators.http import require_POST

from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError

from .credit_services import (
    assess_credit_application,
    decide_credit_application,
    approve_credit, repay_credit,submit_credit_application,
    credit_summary,
    eligibility_problem,
)

from .utils import normalize_phone, throttled

from django.contrib.auth.models import Group
from django.contrib.auth.views import PasswordChangeView

from django.db import transaction
from django.urls import reverse_lazy
from django.utils.crypto import get_random_string
from django.views.decorators.cache import never_cache

from .forms import SetPinForm, StaffCreateForm, CloseAccountForm, ProfileForm

from .services import CloseBlocked, close_customer_account, closure_blockers

from cards.models import BankCard, CardOrder

from transactions.summary import summarize
from django.conf import settings
from django import forms

from face_auth import service as face





MAX_AMOUNT = Decimal("10000000")


def parse_amount(raw):
    try:
        amount = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise ValidationError("Enter a valid amount.")
    if not amount.is_finite() or amount <= 0:
        raise ValidationError("Enter an amount above zero.")
    if amount > MAX_AMOUNT:
        raise ValidationError("Amount is too large.")
    if amount != amount.quantize(Decimal("0.01")):
        raise ValidationError("Use at most 2 decimal places.")
    return amount


@never_cache
@login_required
def close_account_view(request):
    account = get_object_or_404(Account, user=request.user)
    if account.is_closed:
        return redirect("accounts:login")

    blockers = closure_blockers(account)
    form = CloseAccountForm(request.POST or None, user=request.user)

    if request.method == "POST":
        if throttled(request, "close", 5):
            return render(request, "accounts/form_page.html", {
                "title": "Close your account", "form": form, "button": "Close my account",
                "error": "Too many attempts. Try again later."}, status=429)
        if not blockers and form.is_valid():
            pin = form.cleaned_data["pin"]
            if account.is_locked():
                form.add_error("pin", "Too many wrong attempts. Try again later.")
            elif not account.check_pin(pin):
                form.add_error("pin", "Incorrect PIN.")
            else:
                try:
                    close_customer_account(request.user, account)
                except CloseBlocked as e:
                    blockers = [str(e)]
                else:
                    logout(request)
                    messages.success(request, "Your account is closed. Thank you for banking with Klosa.")
                    return redirect("accounts:login")

    return render(request, "accounts/form_page.html", {
        "title": "Close your account", "form": form, "button": "Close my account",
        "error": blockers[0] if blockers else ""})


@login_required
def set_pin_view(request):
    account = get_object_or_404(Account, user=request.user)
    form = SetPinForm(request.POST or None, user=request.user, has_pin=bool(account.pin_hash))
    if request.method == "POST" and form.is_valid():
        account.set_pin(form.cleaned_data["pin"])
        messages.success(request, "Transaction PIN saved.")
        return redirect("accounts:onboarding")
    return render(request, "accounts/form_page.html", {
        "title": "Set transaction PIN", "form": form, "button": "Save PIN"})


@never_cache
@login_required
def staff_create_view(request):
    if not request.user.is_superuser:                  # only superusers create staff
        raise PermissionDenied
    form = StaffCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        temp = get_random_string(12)
        with transaction.atomic():
            staff = form.save(commit=False)
            staff.username = staff.email               # staff log in with their email
            staff.is_staff = True
            staff.is_superuser = False                 # never granted from this form
            staff.must_change_password = True
            staff.set_password(temp)
            staff.save()
            staff.groups.add(Group.objects.get(name=form.cleaned_data["role"]))
        return render(request, "accounts/staff_created.html",
                      {"staff": staff, "temp_password": temp})
    return render(request, "accounts/form_page.html", {
        "title": "Create staff user", "form": form, "button": "Create staff"})


class ForcedPasswordChangeView(PasswordChangeView):
    template_name = "accounts/form_page.html"
    success_url = reverse_lazy("accounts:home")
    extra_context = {"title": "Choose a new password", "button": "Save password"}

    def form_valid(self, form):
        response = super().form_valid(form)
        user = self.request.user
        user.must_change_password = False
        user.save(update_fields=["must_change_password"])
        return response



@login_required
@require_POST
def logout_view(request):
    logout(request)
    messages.success(request, "You have been logged out successfully.")
    return redirect("accounts:login")



def login_view(request):
    if request.method != "POST":
        return render(request, "accounts/login.html")
    username = request.POST.get("username", "")
    ident = normalize_phone(username) or username.lower()
    if throttled(request, f"login:{ident}", 10):
        return render(request, "accounts/login.html", {"error": "Too many attempts. Try again later."}, status=429)
    user = authenticate(request, username=username, password=request.POST.get("password", ""))
    if user is None:
        return render(request, "accounts/login.html", {"error": "Invalid username or password."})
    if user.is_staff:
        login(request, user)
        return redirect("backoffice:home")
    purpose = next((p for p in ("login", "ip_change") if face.required(user, p, request)), None)
    if purpose:
        request.session["face_pending"] = {"uid": user.pk, "purpose": purpose, "backend": user.backend,
                                           "ts": timezone.now().timestamp()}
        return redirect("face_auth:begin")
    login(request, user)
    return redirect("accounts:home")
    
    
 

@login_required
def home_view(request):
    zero = Decimal("0.00")
    account = Account.objects.filter(user=request.user).first()

    context = {
        "account": account,
        "user": request.user,
        "transactions": Transaction.objects.none(),
        "recent_transactions": [],
        "issued_cards": BankCard.objects.none(),
        "pending_card_orders": CardOrder.objects.none(),
        "beneficiaries": [],
        "total_income": zero,
        "total_expenses": zero,
        "monthly_spending": zero,
        "payment_total": zero,
        "transfer_total": zero,
        "withdrawal_total": zero,
        "payment_percent": 0,
        "transfer_percent": 0,
        "withdrawal_percent": 0,
    }
    if not account:
        return render(request, "accounts/account.html", context)

    month_start = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    overall = summarize(account)
    month = summarize(account, since=month_start)
    spent = month["expenses"]
    pct = lambda part: (part / spent * 100) if spent else 0

    latest = Transaction.objects.filter(account=account).order_by("-created_at")[:50]

    context.update({
        "transactions": latest,
        "recent_transactions": latest[:5],
        "issued_cards": BankCard.objects.filter(account=account).order_by("-created_at"),
        "pending_card_orders": (
            CardOrder.objects
            .filter(account=account, status__in=["pending", "approved", "issuing"])
            .select_related("bank_card")
            .prefetch_related("delivery")
            .order_by("-created_at")
        ),
        "beneficiaries": Beneficiary.objects.filter(owner=request.user, is_active=True).order_by("name"),
        "total_income": overall["income"],
        "total_expenses": overall["expenses"],
        "monthly_spending": spent,
        "payment_total": month["bills"],
        "transfer_total": month["transfers"],
        "withdrawal_total": month["withdrawals"],
        "payment_percent": pct(month["bills"]),
        "transfer_percent": pct(month["transfers"]),
        "withdrawal_percent": pct(month["withdrawals"]),
    })
    
    has_credit_card = BankCard.objects.filter(account=account, card_type="credit").exclude(
        status__in=["cancelled", "expired"]).exists()
    context.update({"credit_info": credit_summary(account), "has_credit_card": has_credit_card,
                    "sim": getattr(settings, "CARDS_ALLOW_SIMULATOR", False)})
    return render(request, "accounts/account.html", context)
   


@login_required
def profile_view(request):
    account = get_object_or_404(Account, user=request.user)

    if request.method == "POST":
        form = ProfileForm(request.POST, instance=request.user)

        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated successfully.")
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=request.user)

    return render(request, "accounts/profile.html", {
        "form": form,
        "account": account,
    })
    
    
    
class CreditAskForm(forms.Form):
    amount = forms.CharField(label="Credit limit you would like (₦)", max_length=15,
                             widget=forms.TextInput(attrs={"inputmode": "decimal"}))


@login_required
def credit_request_view(request):
    get_object_or_404(Account, user=request.user)
    if CreditApplication.objects.filter(user=request.user, status__in=("pending", "assessed")).exists():
        messages.info(request, "Your credit request is being reviewed.")
        return redirect("accounts:credit")
    form = CreditAskForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            submit_credit_application(user=request.user, requested_amount=form.cleaned_data["amount"])
        except ValidationError as e:
            form.add_error("amount", e.messages[0])
        else:
            messages.success(request, "Your credit request was sent. We will review it.")
            return redirect("accounts:credit")
    return render(request, "accounts/form_page.html", {"title": "Request credit", "form": form, "button": "Send request"})
    
    

@login_required
def credit_view(request):
    account = get_object_or_404(Account, user=request.user)
    credit = CreditAccount.objects.filter(account=account).first()
    app = CreditApplication.objects.filter(user=request.user).first()
    card = BankCard.objects.filter(account=account, card_type="credit").exclude(status__in=["cancelled", "expired"]).first()
    order = CardOrder.objects.filter(account=account, card_type="credit", status__in=("pending", "approved", "issuing")).first()
    summary = credit_summary(account)
    ctx = {"account": account, "credit": credit, "application": app, "credit_card": card, "card_order": order,
           "summary": summary, "problem": eligibility_problem(account) if not summary else None,
           "recent": CreditTransaction.objects.filter(credit_account=credit)[:8] if credit else []}
    return render(request, "accounts/credit.html", ctx)  
    
 
    
@login_required
def credit_repay_view(request):
    account = get_object_or_404(Account, user=request.user)
    credit = CreditAccount.objects.filter(account=account, status__in=["active", "suspended"]).first()
    if not credit:
        messages.error(request, "No credit facility found.")
        return redirect("accounts:credit")
    ref = request.session.setdefault("cr_ref", "KLR" + uuid.uuid4().hex[:16].upper())

    if request.method == "POST":
        try:
            amount = parse_amount(request.POST.get("amount", ""))
            repay_credit(credit, amount, reference=ref, pin=request.POST.get("pin", ""))
            request.session.pop("cr_ref", None)
            messages.success(request, "Repayment successful.")
            return redirect("accounts:credit")
        except ValidationError as e:
            messages.error(request, e.messages[0])
    return render(request, "accounts/credit_repay.html", {"account": account, "credit": credit})
  
  
@login_required
def credit_applications_view(request):
    applications = CreditApplication.objects.filter(
        user=request.user
    ).order_by("-submitted_at")

    return render(
        request,
        "accounts/credit_applications.html",
        {
            "applications": applications,
        },
    )
    
    

@login_required
def credit_staff_dashboard_view(request):
    if not request.user.is_staff:
        raise PermissionDenied

    can_assess = request.user.has_perm(
        "accounts.assess_creditapplication"
    )
    can_approve = request.user.has_perm(
        "accounts.approve_creditapplication"
    )

    if not can_assess and not can_approve:
        raise PermissionDenied

    pending_applications = CreditApplication.objects.filter(
        status="pending"
    ).select_related("user").order_by("submitted_at")

    assessed_applications = CreditApplication.objects.filter(
        status="assessed"
    ).select_related(
        "user", "assessed_by"
    ).order_by("submitted_at")

    return render(
        request,
        "accounts/staff_credit_dashboard.html",
        {
            "pending_applications": pending_applications,
            "assessed_applications": assessed_applications,
            "can_assess": can_assess,
            "can_approve": can_approve,
        },
    )


@login_required
@require_POST
def assess_credit_application_view(request, application_id):
    try:
        assess_credit_application(
            application_id=application_id,
            assessor=request.user,
            assessed_amount=request.POST.get("assessed_amount"),
            reason=request.POST.get("assessment_reason", ""),
        )

        messages.success(
            request,
            "Assessment submitted successfully. "
            "It is awaiting an independent final decision.",
        )

    except (ValidationError, PermissionDenied) as e:
        message = (
            e.messages[0]
            if isinstance(e, ValidationError)
            else str(e)
        )
        messages.error(request, message)

    return redirect("accounts:staff_credit_dashboard")


@login_required
@require_POST
def decide_credit_application_view(request, application_id):
    try:
        decide_credit_application(
            application_id=application_id,
            approver=request.user,
            decision=request.POST.get("decision"),
            reason=request.POST.get("decision_reason", ""),
        )

        messages.success(
            request,
            "Credit application decision recorded.",
        )

    except (ValidationError, PermissionDenied) as e:
        message = (
            e.messages[0]
            if isinstance(e, ValidationError)
            else str(e)
        )
        messages.error(request, message)

    return redirect("accounts:staff_credit_dashboard")
