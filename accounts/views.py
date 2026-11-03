
from django.contrib.auth.decorators import login_required

from .models import Account, CreditAccount
from transactions.models import Transaction, CreditTransaction
from cards.models import BankCard
from beneficiary.models import Beneficiary

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout

from django.db.models import Sum, Q

from django.utils import timezone
from django.db.models.functions import Coalesce

from django.contrib import messages
from django.views.decorators.http import require_POST

from .forms import ProfileForm

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from .credit_services import approve_credit, repay_credit



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
    account = Account.objects.filter(
        user=request.user
    ).first()

    transactions = []
    cards = []
    beneficiaries = []

    if account:
       
        cards = BankCard.objects.filter(
            account=account
        ).order_by("-created_at")[:3]

        beneficiaries = Beneficiary.objects.filter(
            owner=request.user
        ).order_by("name")
        
        
        now = timezone.localtime()
        month_start = now.replace(
    day=1,
    hour=0,
    minute=0,
    second=0,
    microsecond=0
)

        monthly_transactions = account.transactions.filter(
    created_at__gte=month_start,
    created_at__lte=now
)

        monthly_totals = monthly_transactions.aggregate(payments=Coalesce(Sum("amount",filter=Q(transaction_type="payment")),
        Decimal("0.00")
    ), withdrawals=Coalesce(Sum("amount", filter=Q(transaction_type="withdrawal")),
        Decimal("0.00")
    )
)

        payment_total = monthly_totals["payments"]
        withdrawal_total = monthly_totals["withdrawals"]

        monthly_spending = payment_total + withdrawal_total

        total = payment_total + withdrawal_total

        payment_percent = (
    payment_total / total * 100 if total else 0
)

        withdrawal_percent = (
    withdrawal_total / total * 100 if total else 0
)

        transactions = Transaction.objects.filter(
    account=request.user.account
).order_by("-created_at")

        totals = transactions.aggregate(income=Sum(
        "amount",filter=Q(transaction_type="deposit")
    ),
        expenses=Sum(
        "amount",filter=Q(transaction_type__in=["withdrawal", "payment"])
    )
)

    context = {
        "account": account,
        "transactions": transactions,
        "cards": cards,
        "beneficiaries": beneficiaries,
        "recent_transactions": transactions[:3],        "total_income": totals["income"] or Decimal("0.00"),
        "total_expenses": totals["expenses"] or Decimal("0.00"),
    }
    
    context.update({
    "monthly_spending": monthly_spending,
    "payment_total": payment_total,
    "withdrawal_total": withdrawal_total,
    "payment_percent": payment_percent,
    "withdrawal_percent": withdrawal_percent,
    "user": request.user,
})

    return render(
        request,
        "accounts/account.html",
        context,
    )
    
  

#@login_required
#def settings_view(request):
  #  account = get_object_or_404(
      #  Account,
    #    user=request.user
#    )

    #context = {
        #"account": account,
   #     "user": request.user,
  #  }

 #   return render(request, "accounts/settings.html", context)
    


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
        try:
            credit = approve_credit(account)

            messages.success(
                request,
                "Your demo credit facility has been approved."
            )
            return redirect("accounts:credit")

        except ValidationError as e:
            messages.error(request, e.messages[0])

        return redirect("accounts:credit_apply")

    return render(request, "accounts/credit_apply.html", {
        "account": account,
    })
    
    
    
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