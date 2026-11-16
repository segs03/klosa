from .models import AuditLog


def log(staff, action, target="", detail=""):
    AuditLog.objects.create(staff=staff, action=action[:40], target=str(target)[:60], detail=(detail or "")[:300])