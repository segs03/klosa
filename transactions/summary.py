from decimal import Decimal

from django.db.models import Q, Sum
from django.db.models.functions import Coalesce

from biller.models import BillPayment
from .models import Transaction

ZERO = Decimal("0.00")

from cashier.models import Withdrawal

    

def summarize(account, since=None):
    """Income and spending that match what the person actually experienced.
    Income: money received. Spending: bills, transfers and withdrawals.
    Savings moves are neither. A refunded bill, and its refund, count nowhere."""
    rows = Transaction.objects.filter(account=account)
    if since is not None:
        rows = rows.filter(created_at__gte=since)

    undone = list(BillPayment.objects.filter(account=account, status="reversed").values_list("reference", flat=True))
    undone += list(Withdrawal.objects.filter(account=account, status="reversed").values_list("reference", flat=True))

    kept = ~Q(reference__in=undone) if undone else Q()
    is_income = Q(transaction_type="deposit") & ~Q(description__startswith="Refund:")

    def spent(*types):
        return Q(transaction_type__in=types) & kept

    def total(q):
        return Coalesce(Sum("amount", filter=q), ZERO)

    return rows.aggregate(
        income=total(is_income),
        expenses=total(spent(*Transaction.EXPENSE_TYPES)),
        bills=total(spent("payment")),
        transfers=total(spent("transfer")),
        withdrawals=total(spent("withdrawal")),
    )