import ipaddress
from urllib.parse import quote

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.urls import Resolver404, resolve, reverse

GATES = {"biller": "bills", "cashier": "cashier", "cards": "cards"}
OPEN_PREFIXES = ("/accounts/login", "/accounts/logout", "/accounts/2fa/", "/static/", "/healthz/")
CSP = ("default-src 'self'; img-src 'self' data:; font-src 'self'; style-src 'self' 'unsafe-inline'; "
       "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; "
       "base-uri 'self'; object-src 'none'; manifest-src 'self'; worker-src 'self'")


def features(request):
    return {"features": getattr(settings, "FEATURES", {})}


def healthz(request):
    """For the load balancer. It says only yes or no, never why."""
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
        cache.set("healthz", "1", 5)
        ok = cache.get("healthz") == "1"
    except Exception:
        ok = False
    return JsonResponse({"ok": ok}, status=200 if ok else 503)


class RealIPMiddleware:
    """Behind nginx the connection comes from nginx. Trust X-Real-IP only when Django is reachable
    through nginx alone (a unix socket), or anyone could spoof it."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(settings, "TRUST_REAL_IP_HEADER", False):
            try:
                request.META["REMOTE_ADDR"] = str(ipaddress.ip_address(request.META.get("HTTP_X_REAL_IP", "").strip()))
            except ValueError:
                pass
        return self.get_response(request)


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, private"      # the back button must not show a balance
            response.headers["Pragma"] = "no-cache"
        return response


class FeatureGateMiddleware:
    """A switched-off feature does not exist: its pages and webhooks answer 404."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            match = resolve(request.path_info)
        except Resolver404:
            return self.get_response(request)
        feature = GATES.get(match.app_name)
        if match.app_name == "accounts" and (match.url_name or "").startswith("credit"):
            feature = "credit"
        if feature and not settings.FEATURES.get(feature, False):
            raise Http404
        return self.get_response(request)


class StaffTwoFactorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (getattr(settings, "STAFF_2FA_REQUIRED", False) and user is not None and user.is_authenticated
                and user.is_staff and not request.session.get("2fa_ok")
                and not request.path.startswith(OPEN_PREFIXES)):
            return redirect(f"{reverse('accounts:twofa')}?next={quote(request.get_full_path())}")
        return self.get_response(request)