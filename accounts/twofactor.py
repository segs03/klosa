from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from backoffice.events import record
from . import totp
from .models import StaffTOTP
from .utils import hit


@login_required
def twofa(request):
    user = request.user
    if not user.is_staff:
        raise PermissionDenied
    nxt = request.GET.get("next") or request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        nxt = ""
    dest = nxt or "backoffice:home"
    dev = StaffTOTP.objects.filter(user=user).first()
    if request.session.get("2fa_ok") and dev and dev.confirmed:
        return redirect(dest)
    if dev is None:
        dev = StaffTOTP.create_for(user)
    setup, error = not dev.confirmed, ""

    if request.method == "POST":
        if hit(f"2fa:{user.pk}", 6, 600):
            error = "Too many attempts. Wait ten minutes."
            record(request, "2fa_locked", user=user)
        else:
            step = totp.verify(dev.secret(), request.POST.get("code", ""), dev.last_step)
            won = bool(step) and StaffTOTP.objects.filter(pk=dev.pk, last_step__lt=step).update(last_step=step) == 1
            if won:
                if setup:
                    StaffTOTP.objects.filter(pk=dev.pk).update(confirmed=True)
                request.session["2fa_ok"] = True
                request.session.cycle_key()
                record(request, "2fa_setup" if setup else "2fa_ok", user=user)
                return redirect(dest)
            error = "That code is wrong or was already used."
            record(request, "2fa_failed", user=user)

    ctx = {"setup": setup, "error": error, "next": nxt}
    if setup:
        ctx.update(secret=dev.secret(), uri=totp.uri(dev.secret(), user.get_username()))
    return render(request, "accounts/twofa.html", ctx)