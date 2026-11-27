from django.core.management.base import BaseCommand

from assetfund import services


class Command(BaseCommand):
    help = "Expire offers nobody accepted and mark advances that passed their term. Run it daily."

    def handle(self, *args, **options):
        expired = services.expire_offers()
        overdue = services.mark_overdue()
        self.stdout.write(f"Offers expired: {expired}. Advances marked overdue: {overdue}.")
