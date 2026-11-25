from django.core.management.base import BaseCommand

from funding.models import LedgerAccount

CHART = [("1000", "Operating cash", "asset"), ("2000", "Investor capital", "liability"),
         ("2100", "Company capital", "equity"), ("2200", "Investor retained allocations", "liability"),
         ("3000", "Retained earnings", "equity"), ("3100", "Loss allocation clearing", "equity"),
                  ("2300", "Customer deposits", "liability"), ("5200", "Welcome bonus expense", "expense"),
         ("5300", "Savings interest expense", "expense")
         ]


class Command(BaseCommand):
    help = "Create the ledger accounts the funding code posts to"

    def handle(self, *args, **opts):
        for code, name, kind in CHART:
            LedgerAccount.objects.get_or_create(code=code, defaults={"name": name, "account_type": kind})
        self.stdout.write(f"{len(CHART)} ledger accounts are in place.")