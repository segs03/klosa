from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from cashier.models import Deposit, Withdrawal
from cashier.services import apply_payout, credit_deposit, get_provider, settle_withdrawal


class Command(BaseCommand):
    help = "Check pending deposits and withdrawals with the provider"

    def add_arguments(self, parser):
        parser.add_argument("--min-age", type=int, default=5, help="minutes before an item is checked")

    def handle(self, *args, **opts):
        now = timezone.now()
        cutoff = now - timedelta(minutes=opts["min_age"])
        provider = get_provider()

        for d in Deposit.objects.filter(status="pending", created_at__lt=cutoff):
            try:
                r = provider.verify_deposit(d)
                if r.status == "success":
                    credit_deposit(d.reference, r.amount, r.provider_reference)
                elif r.status == "failed":
                    Deposit.objects.filter(pk=d.pk, status="pending").update(status="failed")
                elif d.created_at < now - timedelta(hours=24):
                    Deposit.objects.filter(pk=d.pk, status="pending").update(status="expired")
            except Exception as e:
                self.stderr.write(f"{d.reference}: {e}")

        open_orders = Withdrawal.objects.filter(status="processing")
        for w in open_orders.filter(submitted_at__isnull=True, created_at__lt=cutoff):
            settle_withdrawal(w.pk)                      # never reached the bank, so send it now
        for w in open_orders.filter(submitted_at__lt=cutoff):
            try:
                apply_payout(w.pk, provider.requery_payout(w))
            except Exception as e:
                self.stderr.write(f"{w.reference}: {e}")

        stuck = open_orders.filter(submitted_at__lt=now - timedelta(hours=24)).count()
        if stuck:
            self.stdout.write(f"{stuck} withdrawal(s) are older than 24 hours. Staff must review them.")