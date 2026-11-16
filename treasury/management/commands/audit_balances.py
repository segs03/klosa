
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum

from accounts.models import Account
from treasury.services import audit_balances


class Command(BaseCommand):
    help = "Check that every balance equals the sum of its transactions"

    def handle(self, *args, **opts):
        bad = audit_balances()
        negative = Account.objects.filter(balance__lt=0).count()
        total = Account.objects.aggregate(t=Sum("balance"))["t"] or 0
        self.stdout.write(f"accounts holding ₦{total:,.2f} in total")
        for number, balance, expected in bad:
            self.stderr.write(f"MISMATCH {number}: balance {balance}, transactions say {expected}")
        if bad or negative:
            raise CommandError(f"{len(bad)} mismatched account(s), {negative} negative balance(s)")
        self.stdout.write("All balances match their transactions.")