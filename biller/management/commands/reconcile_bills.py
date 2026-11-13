from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from biller.models import BillPayment
from biller.services import apply_result, get_provider, settle


class Command(BaseCommand):
    help = "Settle orders that are still processing"

    def add_arguments(self, parser):
        parser.add_argument("--min-age", type=int, default=2, help="minutes before an order is checked")

    def handle(self, *args, **opts):
        cutoff = timezone.now() - timedelta(minutes=opts["min_age"])
        provider = get_provider()
        open_orders = BillPayment.objects.filter(status="processing")

        for o in open_orders.filter(submitted_at__isnull=True, created_at__lt=cutoff):
            settle(o.pk)                                 # never reached the provider, so send it now

        for o in open_orders.filter(submitted_at__lt=cutoff):
            try:
                apply_result(o.pk, provider.requery(o))
            except Exception as e:
                self.stderr.write(f"{o.reference}: {e}")

        stuck = open_orders.filter(submitted_at__lt=timezone.now() - timedelta(hours=24)).count()
        if stuck:
            self.stdout.write(f"{stuck} order(s) are older than 24 hours. Staff must review them.")