from django.core.management.base import BaseCommand

from accounts.bonus import send_credit_offer
from accounts.credit_services import eligibility_problem
from accounts.models import Account, CreditAccount, CreditApplication


class Command(BaseCommand):
    help = "Tell verified customers once when they qualify to request credit"

    def handle(self, *args, **opts):
        sent = 0
        accounts = Account.objects.filter(closed_at__isnull=True, user__is_active=True,
                                          user__verified_identity__isnull=False).select_related("user")
        for acc in accounts:
            if CreditAccount.objects.filter(account=acc, status__in=("active", "suspended")).exists():
                continue
            if CreditApplication.objects.filter(user=acc.user, status__in=("pending", "assessed")).exists():
                continue
            if eligibility_problem(acc) is None and send_credit_offer(acc.user, acc):
                sent += 1
        self.stdout.write(f"credit offers sent: {sent}")