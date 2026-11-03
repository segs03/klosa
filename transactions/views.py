
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Q
from django.shortcuts import get_object_or_404, render

from accounts.models import Account, CreditAccount
from .models import Transaction


@login_required
def transaction_list(request):
    account = get_object_or_404(
        Account,
        user=request.user
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
        "transactions": transactions,
        "recent_transactions": transactions[:3],        "total_income": totals["income"] or Decimal("0.00"),
        "total_expenses": totals["expenses"] or Decimal("0.00"),
    }

    return render(
        request,
        "transactions/index.html",
        context
    )


@login_required
def credit_transactions_view(request):
    account = get_object_or_404(Account, user=request.user)

    credit = CreditAccount.objects.filter(
        account=account
    ).first()

    transactions = []

    if credit:
        transactions = credit.transactions.all().order_by("-created_at")

    return render(request, "transactions/credit_transactions.html", {
        "credit": credit,
        "transactions": transactions,
    })


@login_required
def receipt(request, reference):
    tx = get_object_or_404(Transaction, reference=reference,
                           account__user=request.user, transaction_type="transfer")
    return render(request, "transaction/receipt.html", {"tx": tx})