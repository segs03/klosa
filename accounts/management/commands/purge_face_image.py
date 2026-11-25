from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.face import storage
from accounts.models import FaceFrame


class Command(BaseCommand):
    help = "Delete face images 30 days after the decision"

    def handle(self, *args, **opts):
        cutoff = timezone.now() - timedelta(days=30)
        old = FaceFrame.objects.exclude(path="").filter(check__reviewed_at__lt=cutoff)
        n = 0
        for f in old:
            storage.delete(f.path)
            FaceFrame.objects.filter(pk=f.pk).update(path="")
            n += 1
        self.stdout.write(f"images deleted: {n}")