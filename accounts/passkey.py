import base64
import json
import traceback

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from webauthn import (
    generate_authentication_options, generate_registration_options,
    options_to_json, verify_authentication_response, verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
    ResidentKeyRequirement, UserVerificationRequirement,
)

from .models import Passkey

REG_KEY, AUTH_KEY = "webauthn_reg_challenge", "webauthn_auth_challenge"
MAX_FAILS, LOCK_SECONDS = 10, 15 * 60


def _json(options):
    return HttpResponse(options_to_json(options), content_type="application/json")


def _fail_key(request):
    return f"passkey_fail:{request.META.get('REMOTE_ADDR', '')}"


# ---------- add a passkey (user must already be logged in) ----------
@login_required
@require_POST
def passkey_register_options(request):
    user = request.user
    options = generate_registration_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        rp_name=settings.WEBAUTHN_RP_NAME,
        user_id=str(user.pk).encode(),
        user_name=user.username,
        user_display_name=user.get_full_name() or user.username,
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.credential_id))
            for p in user.passkeys.all()
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    request.session[REG_KEY] = bytes_to_base64url(options.challenge)
    return _json(options)



@login_required
@require_POST
def passkey_register_verify(request):
    challenge = request.session.pop(REG_KEY, None)
    if not challenge:
        print("PASSKEY REGISTER FAILED: no challenge in session")
        return JsonResponse({"error": "Session expired. Try again."}, status=400)
    try:
        body = json.loads(request.body)
        v = verify_registration_response(
            credential=body["credential"],
            expected_challenge=base64url_to_bytes(challenge),
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGIN,
            require_user_verification=True,
        )
    except Exception as e:
        traceback.print_exc()
        print("PASSKEY REGISTER FAILED:", repr(e))
        return JsonResponse({"error": "Could not verify this passkey."}, status=400)

    Passkey.objects.create(
        user=request.user,
        credential_id=bytes_to_base64url(v.credential_id),
        public_key=v.credential_public_key,
        sign_count=v.sign_count,
        name=(body.get("name") or "My device")[:60],
    )
    print("PASSKEY REGISTER OK for", request.user)
    return JsonResponse({"ok": True})

# ---------- sign in with a passkey (anonymous) ----------
@require_POST
def passkey_login_options(request):
    options = generate_authentication_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    request.session[AUTH_KEY] = bytes_to_base64url(options.challenge)
    return _json(options)


@require_POST
def passkey_login_verify(request):
    key = _fail_key(request)
    if cache.get(key, 0) >= MAX_FAILS:
        return JsonResponse({"error": "Too many attempts. Try later."}, status=429)

    def fail():
        cache.add(key, 0, LOCK_SECONDS)
        cache.incr(key)
        return JsonResponse({"error": "Passkey sign-in failed."}, status=400)

    challenge = request.session.pop(AUTH_KEY, None)    # single use
    try:
        body = json.loads(request.body)
        cred = body["credential"]
    except Exception:
        return fail()

    pk = Passkey.objects.select_related("user").filter(
        credential_id=cred.get("id", "")).first()
    if not challenge or not pk or not pk.user.is_active:
        return fail()

    # the passkey must belong to the user it claims to be
    handle = (cred.get("response") or {}).get("userHandle")
    if handle:
        try:
            if base64url_to_bytes(handle) != str(pk.user.pk).encode():
                return fail()
        except Exception:
            return fail()

    try:
        v = verify_authentication_response(
            credential=cred,
            expected_challenge=base64url_to_bytes(challenge),
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGIN,
            credential_public_key=bytes(pk.public_key),
            credential_current_sign_count=pk.sign_count,
            require_user_verification=True,
        )
    except Exception:
        return fail()

    pk.sign_count = v.new_sign_count
    pk.last_used_at = timezone.now()
    pk.save(update_fields=["sign_count", "last_used_at"])

    cache.delete(key)
    login(request, pk.user, backend="django.contrib.auth.backends.ModelBackend")

    nxt = body.get("next") or ""
    ok = url_has_allowed_host_and_scheme(
        nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    return JsonResponse({"ok": True, "next": nxt if ok else reverse("accounts:home")})