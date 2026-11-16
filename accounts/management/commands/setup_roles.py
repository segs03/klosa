from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from accounts.roles import ROLES


class Command(BaseCommand):
    help = "Create staff role groups"

    def handle(self, *args, **opts):
        for name, codes in ROLES.items():
            group, _ = Group.objects.get_or_create(name=name)
            perms = Permission.objects.filter(codename__in=codes)
            group.permissions.set(perms)
            missing = sorted(set(codes) - set(perms.values_list("codename", flat=True)))
            self.stdout.write(f"{name}: {perms.count()} permissions" + (f" (missing: {', '.join(missing)})" if missing else ""))