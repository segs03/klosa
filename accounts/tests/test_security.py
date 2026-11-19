import time
import unittest
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection, transaction
from django.http import HttpResponse
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import totp
from accounts.models import Account, StaffTOTP
from accounts.security import RealIPMiddleware
from backoffice.models import SecurityEvent
from transactions.models import Transaction

User = get_user_model()
SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
ALL_OFF = {"bills": False, "cashier": False, "cards": False, "credit": False}
ALL_ON = {"bills": True, "cashier": True, "cards": True, "credit": True}


class TotpTests(SimpleTestCase):
    def test_rfc_6238_test_vectors(self):
        self.assertEqual(totp._code(SECRET, 59 // 30), "287082")
        self.assertEqual(totp._code(SECRET, 1111111109 // 30), "081804")

    def test_window_replay_and_junk(self):
        self.assertEqual(totp.verify(SECRET, "287082", now=59), 1)
        self.assertEqual(totp.verify(SECRET, "287082", now=89), 1)            # one step late is fine
        self.assertIsNone(totp.verify(SECRET, "287082", now=59 + 120))        # two minutes late is not
        self.assertIsNone(totp.verify(SECRET, "287082", last_step=1, now=59))  # a used code never works again
        for bad in ("", "12345", "abcdef", "1234567", None):
            self.assertIsNone(totp.verify(SECRET, bad, now=59))

    def test_the_stored_secret_is_encrypted(self):
        token = totp.encrypt(SECRET)
        self.assertNotIn(SECRET.encode(), token)
        self.assertEqual(totp.decrypt(token), SECRET)


@override_settings(STAFF_2FA_REQUIRED=True)
class StaffTwoFactorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user("st", password="pass12345", is_staff=True)
        self.c = Client()
        self.c.login(username="st", password="pass12345")

    def code(self):
        return totp._code(StaffTOTP.objects.get(user=self.staff).secret(), int(time.time() // 30))

    def test_staff_are_sent_to_the_second_step(self):
        r = self.c.get(reverse("backoffice:home"))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith(reverse("accounts:twofa")))

    def test_setup_unlocks_the_console_and_a_code_works_once(self):
        self.c.get(reverse("accounts:twofa"))
        code = self.code()
        self.c.post(reverse("accounts:twofa"), {"code": code})
        self.assertEqual(self.c.get(reverse("backoffice:home")).status_code, 200)
        fresh = Client()
        fresh.login(username="st", password="pass12345")
        fresh.post(reverse("accounts:twofa"), {"code": code})                 # the same code again, new session
        self.assertEqual(fresh.get(reverse("backoffice:home")).status_code, 302)

    def test_a_wrong_code_does_not_unlock_and_guessing_is_stopped(self):
        self.c.get(reverse("accounts:twofa"))
        good = self.code()
        wrong = "000000" if good != "000000" else "111111"
        for _ in range(6):
            self.c.post(reverse("accounts:twofa"), {"code": wrong})
        self.c.post(reverse("accounts:twofa"), {"code": good})                # too late: the limit is hit
        self.assertEqual(self.c.get(reverse("backoffice:home")).status_code, 302)
        self.assertTrue(SecurityEvent.objects.filter(event="2fa_failed").exists())

    def test_customers_are_not_asked(self):
        u = User.objects.create_user("cu", password="pass12345")
        Account.objects.create(user=u, account_number="1000000001")
        c = Client()
        c.login(username="cu", password="pass12345")
        self.assertEqual(c.get(reverse("accounts:home")).status_code, 200)
        self.assertEqual(c.get(reverse("accounts:twofa")).status_code, 403)

    def test_the_login_page_stays_reachable(self):
        self.assertEqual(Client().get(reverse("accounts:login")).status_code, 200)


class FeatureGateTests(TestCase):
    def setUp(self):
        u = User.objects.create_user("cu", password="pass12345")
        Account.objects.create(user=u, account_number="1000000001")
        self.c = Client()
        self.c.login(username="cu", password="pass12345")

    @override_settings(FEATURES=ALL_OFF)
    def test_switched_off_features_do_not_exist(self):
        for name in ("biller:hub", "cashier:deposit", "cards:add", "accounts:credit", "accounts:credit_request"):
            self.assertEqual(self.c.get(reverse(name)).status_code, 404, name)
        self.assertEqual(Client().post(reverse("cashier:webhook")).status_code, 404)

    @override_settings(FEATURES=ALL_ON)
    def test_switched_on_features_load(self):
        self.assertEqual(self.c.get(reverse("biller:hub")).status_code, 200)

    @override_settings(FEATURES=ALL_OFF)
    def test_core_banking_is_never_gated(self):
        for name in ("accounts:home", "transfers:send", "savings:home", "notices:list"):
            self.assertEqual(self.c.get(reverse(name)).status_code, 200, name)


class HeaderAndIpTests(TestCase):
    def test_policy_headers_and_no_store_for_signed_in_pages(self):
        anon = Client().get(reverse("accounts:login"))
        self.assertIn("frame-ancestors 'none'", anon["Content-Security-Policy"])
        self.assertNotIn("no-store", anon.get("Cache-Control", ""))
        u = User.objects.create_user("cu", password="pass12345")
        Account.objects.create(user=u, account_number="1000000001")
        c = Client()
        c.login(username="cu", password="pass12345")
        self.assertIn("no-store", c.get(reverse("accounts:home"))["Cache-Control"])

    def middleware(self, **meta):
        req = RequestFactory().get("/", **meta)
        RealIPMiddleware(lambda r: HttpResponse())(req)
        return req.META["REMOTE_ADDR"]

    @override_settings(TRUST_REAL_IP_HEADER=True)
    def test_the_real_address_is_used_only_when_valid_and_trusted(self):
        self.assertEqual(self.middleware(HTTP_X_REAL_IP="203.0.113.9", REMOTE_ADDR="127.0.0.1"), "203.0.113.9")
        self.assertEqual(self.middleware(HTTP_X_REAL_IP="not-an-ip", REMOTE_ADDR="127.0.0.1"), "127.0.0.1")

    @override_settings(TRUST_REAL_IP_HEADER=False)
    def test_the_header_is_ignored_when_not_trusted(self):
        self.assertEqual(self.middleware(HTTP_X_REAL_IP="203.0.113.9", REMOTE_ADDR="127.0.0.1"), "127.0.0.1")


class EventAndHealthTests(TestCase):
    def test_logins_are_recorded_without_passwords(self):
        User.objects.create_user("cu", password="pass12345")
        c = Client()
        c.post(reverse("accounts:login"), {"username": "cu", "password": "wrong-secret-xyz"})
        e = SecurityEvent.objects.get(event="login_failed")
        self.assertEqual(e.username, "cu")
        self.assertNotIn("wrong-secret-xyz", f"{e.username}{e.detail}{e.agent}")

    def test_health_check_answers_yes_and_nothing_else(self):
        r = Client().get("/healthz/")
        self.assertEqual((r.status_code, r.json()), (200, {"ok": True}))


@unittest.skipUnless(connection.vendor == "postgresql", "needs PostgreSQL")
class LedgerProtectionTests(TestCase):
    def test_money_rows_cannot_be_rewritten_or_deleted(self):
        u = User.objects.create_user("cu", password="x")
        acc = Account.objects.create(user=u, account_number="1000000001")
        t = Transaction.objects.create(account=acc, transaction_type="deposit", amount=Decimal("100"), reference="R1")
        with self.assertRaises(DatabaseError), transaction.atomic():
            Transaction.objects.filter(pk=t.pk).update(amount=Decimal("1"))
        with self.assertRaises(DatabaseError), transaction.atomic():
            Transaction.objects.filter(pk=t.pk).delete()
        Transaction.objects.filter(pk=t.pk).update(created_at=timezone.now())     # harmless columns stay editable
        t.refresh_from_db()
        self.assertEqual(t.amount, Decimal("100"))


class PreflightTests(TestCase):
    def test_the_gate_blocks_a_development_setup(self):
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("preflight", stdout=out)
        self.assertIn("FAIL", out.getvalue())