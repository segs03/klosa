from .models import Notification


def unread(request):
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return {"unread_count": Notification.objects.filter(user=user, read_at__isnull=True).count()}
    return {}