from django.core.management.base import BaseCommand
from django.db.models import Sum

from accounts.models import Account, CreditAccount
from funding.models import JournalLine
from savings.models import SavingsPlan
from treasury.models import CashOut


class Command(BaseCommand):
    help = "What Klosa owes customers, and what customers owe Klosa"

    def handle(self, *args, **opts):
        def total(qs, field):
            return qs.aggregate(t=Sum(field))["t"] or 0
        owed = total(Account.objects.filter(closed_at__isnull=True), "balance")
        saved = total(SavingsPlan.objects.filter(status="active"), "balance")
        held = total(CashOut.objects.filter(status="held"), "amount")
        credit = total(CreditAccount.objects.filter(status__in=("active", "suspended")), "outstanding_balance")
        cash = total(JournalLine.objects.filter(account__code="1000", entry__status="posted"), "debit") \
            - total(JournalLine.objects.filter(account__code="1000", entry__status="posted"), "credit")
        liabilities = owed + saved + held
        self.stdout.write(f"Customer balances     ₦{owed:,.2f}\nSavings               ₦{saved:,.2f}\n"
                          f"Cash withdrawals held ₦{held:,.2f}\nTotal Klosa owes      ₦{liabilities:,.2f}\n"
                          f"Customers owe Klosa   ₦{credit:,.2f}\nLedger cash (1000)    ₦{cash:,.2f}")
        self.stdout.write("WARNING: cash movements are not yet posted to the ledger, so the ledger cash line is not "
                          "a bank balance. Compare Total Klosa owes with the cash you actually hold." if True else "")