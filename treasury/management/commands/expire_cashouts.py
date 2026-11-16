from django.core.management.base import BaseCommand

from treasury.services import expire_cash_outs


class Command(BaseCommand):
    help = "Refund cash withdrawals nobody collected"

    def handle(self, *args, **opts):
        self.stdout.write(f"refunded: {expire_cash_outs()}")