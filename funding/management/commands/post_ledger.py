from django.core.management.base import BaseCommand

from funding.posting import post_pending


class Command(BaseCommand):
    help = "Post new money movements to the ledger"

    def handle(self, *args, **opts):
        posted, skipped = post_pending()
        self.stdout.write(f"posted: {posted}, waiting for an open period: {skipped}")