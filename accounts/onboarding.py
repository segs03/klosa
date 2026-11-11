import secrets

from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render

from .forms import CodeForm, PhoneSignupForm
from .models import Account
from .services import check_code, create_customer_account, make_backup_codes, send_code
from .utils import hit, throttled

User = get_user_model()
BACKEND = "django.contrib.auth.backends.ModelBackend"


def _page(request, title, form, button, status=200, error=""):
    return render(request, "accounts/form_page.html",
                  {"title": title, "form": form, "button": button, "error": error}, status=status)


def make_username():
    while True:
        name = "u" + secrets.token_hex(6)
        if not User.objects.filter(username=name).exists():
            return name


def signup_view(request):
    if request.user.is_authenticated:
        return redirect("accounts:home")
    form = PhoneSignupForm(request.POST or None)
    if request.method == "POST":
        if throttled(request, "signup", 8):
            return _page(request, "Create account", form, "Continue", 429,
                         "Too many attempts. Try again later.")
        if form.is_valid():
            phone, password = form.cleaned_data["phone"], form.cleaned_data["password"]
            request.session["pending_phone"] = phone
            existing = User.objects.filter(phone_number=phone).first()
            if existing and existing.phone_verified:
                return redirect("accounts:verify_phone")     # same screen, no SMS: nobody can probe numbers
            if hit(f"sms:{phone}", 3):                       # 3 codes per number per hour
                return redirect("accounts:verify_phone")
            if existing:
                existing.delete()                            # never verified, replace it
            try:
                with transaction.atomic():
                    user = User(username=make_username(), phone_number=phone, is_active=False)
                    user.set_password(password)
                    user.save()
                send_code(user, "signup")
            except IntegrityError:
                pass
            return redirect("accounts:verify_phone")
    return _page(request, "Create your account", form, "Continue")


def verify_phone_view(request):
    phone = request.session.get("pending_phone")
    if not phone:
        return redirect("accounts:signup")
    form = CodeForm(request.POST or None)
    if request.method == "POST":
        if throttled(request, "verify", 15):
            return _page(request, "Enter your code", form, "Verify", 429, "Too many attempts.")
        if form.is_valid():
            user = User.objects.filter(phone_number=phone, phone_verified=False).first()
            if user and check_code(user, "signup", form.cleaned_data["code"]):
                with transaction.atomic():
                    user.is_active = True
                    user.phone_verified = True
                    user.save()
                    create_customer_account(user)
                request.session.pop("pending_phone", None)
                login(request, user, backend=BACKEND)
                return redirect("accounts:onboarding")
            form.add_error("code", "That code is wrong or expired.")
    return _page(request, "Enter the code we sent you", form, "Verify")


@login_required
def onboarding_view(request):
    account = get_object_or_404(Account, user=request.user)
    return render(request, "accounts/onboarding.html", {
        "has_pin": bool(account.pin_hash),
        "has_passkey": request.user.passkeys.exists(),
        "has_backup": request.user.backup_codes.exists(),
        "tier": account.kyc_tier,
    })


@login_required
def backup_codes_view(request):
    if request.method != "POST":
        return redirect("accounts:onboarding")
    user = request.user
    if user.backup_codes.exists() and not user.check_password(request.POST.get("password", "")):
        return render(request, "accounts/backup_codes.html", {"error": "Wrong password."}, status=403)
    return render(request, "accounts/backup_codes.html", {"codes": make_backup_codes(user)})