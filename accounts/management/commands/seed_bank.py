from decimal import Decimal
from random import choice, randint
from datetime import date

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from accounts.models import Account
from cards.models import BankCard
from beneficiary.models import Beneficiary
from transactions.models import Transaction
from transfers.models import Transfer


User = get_user_model()


class Command(BaseCommand):
    help = "Create Nigerian banking demo data"

    @transaction.atomic
    def handle(self, *args, **options):
        customers = [
            ("adeola_demo", "Adeola", "Adebayo"),
            ("chinedu_demo", "Chinedu", "Okafor"),
            ("amina_demo", "Amina", "Bello"),
            ("tunde_demo", "Tunde", "Adeyemi"),
            ("zainab_demo", "Zainab", "Musa"),
        ]

        banks = [
    ("Access Bank", "044"),
    ("GTBank", "058"),
    ("First Bank", "011"),
    ("UBA", "033"),
    ("Zenith Bank", "057"),
    ("Fidelity Bank", "070"),
]

        accounts = []

        for username, first, last in customers:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "email": f"{username}@example.com",
                },
            )

            if created:
                user.set_password("DemoPass123!")
                user.save()

            account = Account.objects.filter(user=user).first()

            if account is None:
                account = Account.objects.create(
                    user=user,
                    account_number=str(randint(1000000000, 9999999999)),
                    balance=Decimal(
                        choice(["125000.00", "275500.50",
                                "85000.75", "540000.00",
                                "96500.25"])
                    ),
                    currency="NGN",
                )

            accounts.append(account)

            # Demo card. Store only the last four digits.
            
            # Create 3 demo cards per account.
            existing_cards = account.cards.count()

            card_types = ["debit", "credit", "debit"]

            for i in range(existing_cards, 3):
                BankCard.objects.create(
                    account=account,
                    card_type=card_types[i],
                    cardholder_name=f"{first} {last}",
                    last_four=str(randint(1000, 9999)),
                    expiry_month=randint(1, 12),
                    expiry_year=date.today().year + 3,
                    status="active",
                )


            # Demo beneficiary
            
            # Demo beneficiary using the latest Beneficiary model
            
            existing_beneficiary = Beneficiary.objects.filter(
                owner=user,
                name="Demo Recipient",
                is_active=True,
            ).exists()
            
            if not existing_beneficiary:
                bank_name, bank_code = choice(banks)
            
                Beneficiary.objects.create(
                    owner=user,
                    name="Demo Recipient",
                    account_number=str(
                        randint(1000000000, 9999999999)
                    ),
                    bank_name=bank_name,
                    bank_code=bank_code,
                    is_active=True,
                )


        # Create transactions for accounts that have none.
        for account in accounts:
            if not Transaction.objects.filter(account=account).exists():
                for kind, amount, description in [
                    ("deposit", "150000.00", "Demo salary payment"),
                    ("withdrawal", "10000.00", "ATM withdrawal"),
                    ("payment", "4500.00", "Utility bill"),
                ]:
                    Transaction.objects.create(
                        account=account,
                        transaction_type=kind,
                        amount=Decimal(amount),
                        description=description,
                    )

        # Create transfers between demo accounts.
        if len(accounts) >= 2:
            sender = accounts[0]
            receiver = accounts[1]

            if not Transfer.objects.filter(
                sender=sender,
                receiver=receiver,
                reference="DEMO-TRANSFER-001",
            ).exists():
                Transfer.objects.create(
                    sender=sender,
                    receiver=receiver,
                    amount=Decimal("12500.00"),
                    reference="DEMO-TRANSFER-001",
                    status="completed",
                )

        self.stdout.write(
            self.style.SUCCESS(
                "Nigerian banking demo data created successfully."
            )
        )