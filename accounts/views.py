
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
    approve_credit, repay_credit, submit_credit_application
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
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        
        ident = normalize_phone(username) or username.lower()
        
        key = f"login_fail:{ident}:{request.META.get('REMOTE_ADDR', '')}"


        user = authenticate(
            request,
            username=username,
            password=password
        )

        if user is not None:
            login(request, user)
            return redirect("accounts:home")

        return render(request, "accounts/login.html", {
            "error": "Invalid username or password."
        })

    return render(request, "accounts/login.html")
    
    
 

@login_required
def home_view(request):
    account = Account.objects.filter(user=request.user).first()

    transactions = Transaction.objects.none()
    cards = BankCard.objects.none()
    issued_cards = BankCard.objects.none()
    pending_card_orders = CardOrder.objects.none()
    beneficiaries = []

    payment_total = Decimal("0.00")
    withdrawal_total = Decimal("0.00")
    monthly_spending = Decimal("0.00")
    payment_percent = 0
    withdrawal_percent = 0
    total_income = Decimal("0.00")
    total_expenses = Decimal("0.00")

    if account:
        # Issued cards displayed on the dashboard
        issued_cards = BankCard.objects.filter(
            account=account
        ).order_by("-created_at")

        cards = issued_cards[:3]

        # Requests not yet completed
        pending_card_orders = (
            CardOrder.objects
            .filter(
                account=account,
                status__in=["pending", "approved", "issuing"],
            )
            .select_related("bank_card")
            .prefetch_related("carddelivery")
            .order_by("-created_at")
        )

        beneficiaries = Beneficiary.objects.filter(
            owner=request.user
        ).order_by("name")

        now = timezone.localtime()

        month_start = now.replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

        monthly_transactions = account.transactions.filter(
            created_at__gte=month_start,
            created_at__lte=now,
        )

        monthly_totals = monthly_transactions.aggregate(
            payments=Coalesce(
                Sum(
                    "amount",
                    filter=Q(transaction_type="payment"),
                ),
                Decimal("0.00"),
            ),
            withdrawals=Coalesce(
                Sum(
                    "amount",
                    filter=Q(transaction_type="withdrawal"),
                ),
                Decimal("0.00"),
            ),
        )

        payment_total = monthly_totals["payments"]
        withdrawal_total = monthly_totals["withdrawals"]
        monthly_spending = payment_total + withdrawal_total

        if monthly_spending:
            payment_percent = payment_total / monthly_spending * 100
            withdrawal_percent = withdrawal_total / monthly_spending * 100

        transactions = Transaction.objects.filter(
            account=account
        ).order_by("-created_at")

        totals = transactions.aggregate(
            income=Coalesce(
                Sum(
                    "amount",
                    filter=Q(transaction_type="deposit"),
                ),
                Decimal("0.00"),
            ),
            expenses=Coalesce(
                Sum(
                    "amount",
                    filter=Q(
                        transaction_type__in=[
                            "withdrawal",
                            "payment",
                        ]
                    ),
                ),
                Decimal("0.00"),
            ),
        )

        total_income = totals["income"]
        total_expenses = totals["expenses"]

    context = {
        "account": account,
        "transactions": transactions,
        "cards": cards,
        "issued_cards": issued_cards,
        "pending_card_orders": pending_card_orders,
        "beneficiaries": beneficiaries,
        "recent_transactions": transactions[:3],
        "total_income": total_income,
        "total_expenses": total_expenses,
        "monthly_spending": monthly_spending,
        "payment_total": payment_total,
        "withdrawal_total": withdrawal_total,
        "payment_percent": payment_percent,
        "withdrawal_percent": withdrawal_percent,
        "user": request.user,
    }

    return render(request, "accounts/account.html", context)

    
  
"""
@login_required
def settings_view(request):
    account = get_object_or_404(
      Account,
        user=request.user
   )

    context = {
        "account": account,
       "user": request.user,
   }

  return render(request, "accounts/settings.html", context)
    """


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
    
    
    

@login_required
def credit_view(request):
    account = get_object_or_404(
        Account,
        user=request.user
    )

    credit = CreditAccount.objects.filter(
        account=account
    ).first()

    credit_card = BankCard.objects.filter(
        account=account,
        card_type="credit"
    ).first()

    return render(request, "accounts/credit.html", {
        "account": account,
        "credit": credit,
        "credit_card": credit_card,
    })
    
    
 
@login_required
def credit_apply_view(request):
    account = get_object_or_404(Account, user=request.user)

    if request.method == "POST":
        print("CREDIT POST RECEIVED")

        requested_amount = request.POST.get("amount")
        print("REQUESTED AMOUNT:", requested_amount)

        try:
            application = submit_credit_application(
                user=request.user,
                requested_amount=requested_amount,
            )

            print("APPLICATION CREATED:", application.pk)

            return redirect("/accounts/credit/applications/")

        except ValidationError as e:
            print("VALIDATION ERROR:", e)
            messages.error(request, e.messages[0])

        return redirect("accounts:credit_apply")

    return render(
        request,
        "accounts/credit_apply.html",
        {"account": account},
    )

    
@login_required
def credit_repay_view(request):
    account = get_object_or_404(Account, user=request.user)

    credit = CreditAccount.objects.filter(
        account=account,
        status="active"
    ).first()

    if not credit:
        messages.error(request, "No active credit facility found.")
        return redirect("accounts:credit")

    if request.method == "POST":
        try:
            amount = Decimal(request.POST.get("amount", "0"))

            repay_credit(credit, amount)

            messages.success(request, "Repayment successful.")
            return redirect("accounts:credit")

        except (InvalidOperation, TypeError, ValueError):
            messages.error(request, "Enter a valid repayment amount.")

        except ValidationError as e:
            messages.error(request, e.messages[0])

    return render(request, "accounts/credit_repay.html", {
        "account": account,
        "credit": credit,
    })
    
  
  
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
