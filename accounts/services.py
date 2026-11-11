import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Account, BackupCode, IdentityLock, OneTimeCode,  CreditAccount, OneTimeCode
from .utils import digest, new_code, same

MAX_CODE_TRIES = 5

# Placeholder limits. Check your regulator's current KYC rules before going live.
TIER_LIMITS = {
    0: {"single": Decimal("20000"),  "daily": Decimal("50000")},
    1: {"single": Decimal("50000"),  "daily": Decimal("200000")},
    2: {"single": Decimal("200000"), "daily": Decimal("1000000")},
}


def limits_for(account):
    return TIER_LIMITS.get(account.kyc_tier, TIER_LIMITS[0])


# ---------------------------------------------------------- bank account
def generate_account_number():
    for _ in range(20):
        number = str(secrets.randbelow(9 * 10**9) + 10**9)      # 10 digits, no leading zero
        if not Account.objects.filter(account_number=number).exists():
            return number
    raise RuntimeError("Could not generate an account number.")


def create_customer_account(user):
    for _ in range(5):
        try:
            with transaction.atomic():
                return Account.objects.create(
                    user=user, account_number=generate_account_number())
        except IntegrityError:          # two signups picked the same number
            continue
    raise RuntimeError("Could not create the account.")


# ----------------------------------------------------------- messages
def deliver_sms(phone, text):
    if getattr(settings, "SMS_BACKEND", "console") == "console":
        print(f"[SMS to {phone}] {text}")
        return
    raise NotImplementedError("Connect your SMS provider here")


def notify(user, text):
    if user.phone_number:
        deliver_sms(user.phone_number, text)
    if user.email:
        send_mail("Klosa Bank", text, None, [user.email], fail_silently=True)


# ----------------------------------------------------- one-time codes
def send_code(user, purpose, minutes=15):
    OneTimeCode.objects.filter(user=user, purpose=purpose, used=False).update(used=True)
    code = new_code()
    OneTimeCode.objects.create(
        user=user, purpose=purpose, code_hash=digest(purpose, user.pk, code),
        expires_at=timezone.now() + timedelta(minutes=minutes))
    notify(user, f"Klosa Bank code: {code}. It works for {minutes} minutes. Never share it.")


def check_code(user, purpose, code):
    otc = (OneTimeCode.objects
           .filter(user=user, purpose=purpose, used=False, expires_at__gt=timezone.now())
           .order_by("-created_at").first())
    if not otc or otc.attempts >= MAX_CODE_TRIES:
        return False
    if same(otc.code_hash, digest(purpose, user.pk, (code or "").strip())):
        otc.used = True
        otc.save(update_fields=["used"])
        return True
    otc.attempts += 1
    otc.save(update_fields=["attempts"])
    return False


# -------------------------------------------------------- backup codes
def make_backup_codes(user, count=8):
    BackupCode.objects.filter(user=user).delete()
    codes = []
    for _ in range(count):
        c = secrets.token_hex(5)
        BackupCode.objects.create(user=user, code_hash=digest("backup", user.pk, c))
        codes.append(f"{c[:5]}-{c[5:]}")
    return codes


def use_backup_code(user, raw):
    c = (raw or "").replace("-", "").replace(" ", "").lower()
    bc = BackupCode.objects.filter(
        user=user, used_at__isnull=True, code_hash=digest("backup", user.pk, c)).first()
    if not bc:
        return False
    bc.used_at = timezone.now()
    bc.save(update_fields=["used_at"])
    return True


# ------------------------------------------------------ one ID, one account
def register_identity(user, id_type, id_number):
    """Call ONLY after your identity provider confirmed this ID belongs to this person."""
    if IdentityLock.objects.filter(user=user).exists():
        raise ValidationError("An ID is already registered for this account.")
    key = getattr(settings, "IDENTITY_HASH_KEY", "") or None
    h = digest(id_type.upper(), id_number.strip(), key=key)
    try:
        with transaction.atomic():
            IdentityLock.objects.create(user=user, id_hash=h, id_type=id_type.upper())
    except IntegrityError:
        raise ValidationError("This ID is already linked to another account.")
        
        
class CloseBlocked(Exception):
    pass


def closure_blockers(account):
    reasons = []
    if account.balance > 0:
        reasons.append(f"Move your remaining ₦{account.balance:,.2f} out of your account first.")
    credit = CreditAccount.objects.filter(account=account).first()
    if credit and credit.outstanding_balance > 0:
        reasons.append(f"Repay your credit balance of ₦{credit.outstanding_balance:,.2f} first.")
    return reasons


def close_customer_account(user, account):
    with transaction.atomic():
        acc = Account.objects.select_for_update().get(pk=account.pk)    # lock, then re-check
        blockers = closure_blockers(acc)
        if blockers:
            raise CloseBlocked(blockers[0])

        CreditAccount.objects.filter(account=acc).update(status="closed")
        user.passkeys.all().delete()
        BackupCode.objects.filter(user=user).delete()
        OneTimeCode.objects.filter(user=user).delete()

        acc.closed_at = timezone.now()
        acc.save(update_fields=["closed_at"])

        user.is_active = False
        user.phone_number = None             # frees the number for a future signup
        user.email = None
        user.username = f"closed{user.pk}"
        user.set_unusable_password()
        user.save()