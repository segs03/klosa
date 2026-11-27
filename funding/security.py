from django.core.exceptions import PermissionDenied


def need(user, perm):
    if not (user and user.is_authenticated and user.is_active and user.is_staff and user.has_perm(perm)):
        raise PermissionDenied("You are not allowed to do this.")

