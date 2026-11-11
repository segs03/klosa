from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from .utils import normalize_email, normalize_phone


class IdentifierBackend(ModelBackend):
    """Customers log in with a phone number. Staff and admins log in with their email."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        ident = (username or "").strip()
        user = None
        if "@" in ident:
            user = User.objects.filter(email_normalized=normalize_email(ident), is_staff=True).first()
        else:
            phone = normalize_phone(ident)
            if phone:
                user = User.objects.filter(phone_number=phone, is_staff=False).first()
        if user:
            username = user.get_username()
        return super().authenticate(request, username=username, password=password, **kwargs)