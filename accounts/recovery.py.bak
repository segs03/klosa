from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.db import transaction
from django.shortcuts import redirect
from django.utils import timezone

from .forms import PhoneForm, RecoverForm
from .models import Account, BackupCode, RecoveryRequest
from .onboarding import BACKEND, _page
from .services import check_code, notify, send_code, use_backup_code
from .utils import hit, throttled


User = get_user_model()


def _customer(phone):
    return User.objects.filter(phone_number=phone, phone_verified=True,
                               is_active=True, is_staff=False).first()


def recover_request_view(request):
    form = PhoneForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if throttled(request, "recover", 5):
            return _page(request, "Recover your account", form, "Send code", 429, "Too many attempts.")
        phone = form.cleaned_data["phone"]
        user = _customer(phone)
        if user and not hit(f"sms:{phone}", 3):
            send_code(user, "recover")
        request.session["recover_phone"] = phone           # same reply either way
        return redirect("accounts:recover_verify")
    return _page(request, "Recover your account", form, "Send code")


def _finish(request, user, account, new_password):
    with transaction.atomic():
        user.set_password(new_password)          # also signs out every other session
        user.save()
        user.passkeys.all().delete()             # a lost or stolen phone loses its passkey
        BackupCode.objects.filter(user=user).delete()
        account.transfers_locked_until = timezone.now() + timedelta(hours=24)
        account.save(update_fields=["transfers_locked_until"])
        RecoveryRequest.objects.filter(user=user, status__in=["pending", "approved"]).update(status="done")
    notify(user, "Klosa Bank: your password was reset and passkeys removed. Transfers are paused "
                 "for 24 hours. If this was not you, contact support now.")
    login(request, user, backend=BACKEND)
    request.session.pop("recover_phone", None)


def recover_verify_view(request):
    phone = request.session.get("recover_phone")
    if not phone:
        return redirect("accounts:recover")
    form = RecoverForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if throttled(request, "recover_verify", 10):
            return _page(request, "Recover your account", form, "Recover", 429, "Too many attempts.")
        d = form.cleaned_data
        user = _customer(phone)
        account = Account.objects.filter(user=user).first() if user else None
        if user and account:
            approved = check_code(user, "recover_ok", d["code"])      # sent by staff after review
            if approved or check_code(user, "recover", d["code"]):
                strong = approved or (d["backup_code"] and use_backup_code(user, d["backup_code"]))
                if account.kyc_tier >= 1 and not strong:
                    RecoveryRequest.objects.get_or_create(
                        user=user, status="pending", defaults={"reason": "No backup code"})
                    messages.info(request, "Your account is verified, so our team must confirm "
                                           "your identity first. We will contact you.")
                    return redirect("accounts:login")
                _finish(request, user, account, d["new_password"])
                return redirect("accounts:onboarding")
        form.add_error("code", "That code is wrong or expired.")
    return _page(request, "Recover your account", form, "Recover")