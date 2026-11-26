from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from .. import service as fa
from .helpers import SETTINGS, make_user, passed, session


@override_settings(**SETTINGS)
class PassTests(TestCase):
    def setUp(self):
        self.u = make_user("pass")

    def test_no_pass(self):
        self.assertIsNone(fa.satisfied(self.u, "login"))
        self.assertFalse(fa.consume(self.u, "login"))

    def test_pass_works_exactly_once(self):
        passed(self.u)
        self.assertTrue(fa.consume(self.u, "login"))
        self.assertFalse(fa.consume(self.u, "login"))                # replay

    def test_expired_pass(self):
        passed(self.u, age=fa.PASS_VALID + timedelta(seconds=1))
        self.assertFalse(fa.consume(self.u, "login"))

    def test_wrong_purpose(self):
        passed(self.u, "login")
        self.assertFalse(fa.consume(self.u, "phone_change"))

    def test_bound_value_must_match(self):
        passed(self.u, "phone_change", bound="0801")
        self.assertFalse(fa.consume(self.u, "phone_change", "0802"))   # attacker swaps number
        self.assertFalse(fa.consume(self.u, "phone_change"))           # no binding supplied
        self.assertTrue(fa.consume(self.u, "phone_change", "0801"))

    def test_other_users_pass_is_useless(self):
        passed(make_user("victim"))
        self.assertFalse(fa.consume(self.u, "login"))

    def test_failed_review_and_capturing_do_not_count(self):
        for st in ("failed", "review", "capturing", "expired"):
            session(self.u, "login", status=st, decided_at=timezone.now(), bound_hash=fa._h(""))
        self.assertFalse(fa.consume(self.u, "login"))

    def test_staff_passed_without_decided_at_does_not_count(self):
        session(self.u, "recovery", status="passed", decided_at=None, bound_hash=fa._h(""))
        self.assertFalse(fa.consume(self.u, "recovery"))
