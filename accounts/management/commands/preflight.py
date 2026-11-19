from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from accounts.roles import ROLES
from funding.accounting import ACCOUNT_CODES
from funding.models import LedgerAccount
from treasury.services import audit_balances


class Command(BaseCommand):
    help = "Go-live gate. Fails when anything unsafe is switched on."

    def handle(self, *args, **opts):
        s, rows = settings, []

        def check(name, ok, level="FAIL", hint=""):
            rows.append((name, bool(ok), level, hint))

        check("DEBUG is off", not s.DEBUG)
        check("SECRET_KEY is long and not the dev key", len(s.SECRET_KEY) >= 50 and not s.SECRET_KEY.startswith("dev-only"))
        ident = getattr(s, "IDENTITY_HASH_KEY", "")
        check("IDENTITY_HASH_KEY is set and different", ident and ident != s.SECRET_KEY)
        check("TOTP_ENCRYPTION_KEY is set", getattr(s, "TOTP_ENCRYPTION_KEY", ""))
        check("ALLOWED_HOSTS names the real host", s.ALLOWED_HOSTS and "*" not in s.ALLOWED_HOSTS
              and set(s.ALLOWED_HOSTS) - {"localhost", "127.0.0.1"})
        check("HTTPS redirect, secure cookies and HSTS", s.SECURE_SSL_REDIRECT and s.SESSION_COOKIE_SECURE
              and s.CSRF_COOKIE_SECURE and s.SECURE_HSTS_SECONDS >= 31536000)
        check("Shared cache (Redis) for rate limits", not any(k in s.CACHES["default"]["BACKEND"] for k in ("locmem", "dummy")))
        check("Idle session timeout is 30 minutes or less", s.SESSION_COOKIE_AGE <= 1800)
        check("An SMS provider is connected", getattr(s, "SMS_BACKEND", "console") != "console")
        check("Staff second factor is required", s.STAFF_2FA_REQUIRED)
        check("The admin is not at /admin/", s.ADMIN_URL != "admin/", "WARN")
        check("Welcome bonus is off, or its budget is intended", not getattr(s, "WELCOME_BONUS_ENABLED", False), "WARN")
        for name in ("ALLOW_DEMO_CREDIT", "CASHIER_ALLOW_MOCK_PAYPAGE", "CARDS_ALLOW_SIMULATOR"):
            check(f"{name} is off", not getattr(s, name, False))
        for feat, setting in {"bills": "BILLPAY_PROVIDER", "cashier": "CASHIER_PROVIDER", "cards": "CARDS_ISSUER"}.items():
            check(f"{feat}: off, or a real provider", not s.FEATURES.get(feat) or "mock" not in str(getattr(s, setting, "")).lower())

        executor = MigrationExecutor(connection)
        check("No pending migrations", not executor.migration_plan(executor.loader.graph.leaf_nodes()))
        if connection.vendor == "postgresql":
            with connection.cursor() as c:
                c.execute("select count(*) from pg_trigger where tgname = 'klosa_protect_transactions' and not tgisinternal")
                check("Ledger protection trigger is installed", c.fetchone()[0] == 1)
        else:
            check("PostgreSQL is the database", False)
        have = set(LedgerAccount.objects.filter(is_active=True).values_list("code", flat=True))
        check("Ledger accounts exist (run seed_ledger)", set(ACCOUNT_CODES.values()) <= have)
        check("Staff roles exist (run setup_roles)", set(ROLES) <= set(Group.objects.values_list("name", flat=True)))
        no2fa = list(get_user_model().objects.filter(is_staff=True, is_active=True)
                     .exclude(totp__confirmed=True).values_list("username", flat=True))
        check("Every active staff member has a second factor", not no2fa, hint=", ".join(no2fa[:5]))
        bad = audit_balances()
        check("Every balance matches its transactions", not bad, hint=f"{len(bad)} account(s)")

        for name, ok, level, hint in rows:
            self.stdout.write(f"{'PASS' if ok else level:5} {name}" + (f"  [{hint}]" if hint and not ok else ""))
        failed = [r for r in rows if not r[1] and r[2] == "FAIL"]
        if failed:
            raise CommandError(f"{len(failed)} blocking problem(s). Do not go live.")
        self.stdout.write("All blocking checks passed.")