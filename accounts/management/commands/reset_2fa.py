from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from accounts.models import StaffTOTP


class Command(BaseCommand):
    help = "Remove a staff member's authenticator so they can set it up again"

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, username, **opts):
        user = get_user_model().objects.filter(username=username, is_staff=True).first()
        if not user:
            raise CommandError("No staff user with that name.")
        StaffTOTP.objects.filter(user=user).delete()
        self.stdout.write(f"Done. {username} must set up verification at next login.")