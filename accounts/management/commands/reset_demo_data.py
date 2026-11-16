# accounts/management/commands/reset_demo_data.py

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import (
    Account,
    CreditAccount,
    CreditApplication,
)

from transactions.models import (
    Transaction,
    CreditTransaction,
)

from cards.models import (
    BankCard,
    CardOrder,
    CardDelivery,
)


class Command(BaseCommand):
    help = "Remove all demo data and prepare system for production"

    @transaction.atomic
    def handle(self, *args, **options):

        self.stdout.write("Deleting card deliveries...")
        CardDelivery.objects.all().delete()

        self.stdout.write("Deleting card orders...")
        CardOrder.objects.all().delete()

        self.stdout.write("Deleting bank cards...")
        BankCard.objects.all().delete()

        self.stdout.write("Deleting credit transactions...")
        CreditTransaction.objects.all().delete()

        self.stdout.write("Deleting account transactions...")
        Transaction.objects.all().delete()

        self.stdout.write("Deleting credit applications...")
        CreditApplication.objects.all().delete()

        self.stdout.write("Deleting credit facilities...")
        CreditAccount.objects.all().delete()

        self.stdout.write("Resetting balances...")

        Account.objects.update(
            balance=0
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Database reset completed successfully."
            )
        )
