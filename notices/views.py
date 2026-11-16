from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Notification


@login_required
def inbox(request):
    return render(request, "notices/inbox.html", {"items": Notification.objects.filter(user=request.user)[:60]})


@login_required
def open_notice(request, pk):
    n = get_object_or_404(Notification, pk=pk, user=request.user)
    if n.read_at is None:
        n.read_at = timezone.now()
        n.save(update_fields=["read_at"])
    if n.link.startswith("/") and not n.link.startswith("//"):
        return redirect(n.link)
    return redirect("notices:list")


@login_required
@require_POST
def read_all(request):
    Notification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
    return redirect("notices:list")