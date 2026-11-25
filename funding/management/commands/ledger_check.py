from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum

from accounts.models import Account
from funding.models import JournalLine
from savings.models import SavingsPlan
from treasury.models import CashOut


def _sum(qs, field):
    return qs.aggregate(t=Sum(field))["t"] or Decimal("0")


class Command(BaseCommand):
    help = "Compare what Klosa owes customers with the ledger"

    def add_arguments(self, parser):
        parser.add_argument("--strict", action="store_true")

    def handle(self, *args, strict=False, **opts):
        owed = (_sum(Account.objects.all(), "balance") + _sum(SavingsPlan.objects.filter(status="active"), "balance")
                + _sum(CashOut.objects.filter(status="held"), "amount"))
        lines = JournalLine.objects.filter(account__code="2300", entry__status="posted")
        ledger = _sum(lines, "credit") - _sum(lines, "debit")
        diff = owed - ledger
        self.stdout.write(f"Klosa owes customers ₦{owed:,.2f}\nLedger 2300 shows    ₦{ledger:,.2f}\nDifference           ₦{diff:,.2f}")
        if strict and diff != 0:
            raise CommandError("The ledger and the customer balances do not agree.")