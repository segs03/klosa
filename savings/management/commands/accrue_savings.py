from django.core.management.base import BaseCommand

from savings.services import run_daily


class Command(BaseCommand):
    help = "Pay interest and release matured locked plans"

    def handle(self, *args, **opts):
        r = run_daily()
        self.stdout.write(f"matured: {r['matured']}, plans paid interest: {r['plans_paid']}, total: ₦{r['interest']}")