from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from .. import service as fa
from ..models import FaceSession
from .helpers import SETTINGS, make_user


@override_settings(**SETTINGS)
class StartTests(TestCase):
    def setUp(self):
        self.u = make_user("start")

    def test_creates_session_with_distinct_prompts(self):
        s, nonce = fa.start(self.u, "login")
        self.assertEqual(s.status, "capturing")
        self.assertEqual(len(s.prompts), fa.STEPS)
        self.assertEqual(len(set(s.prompts)), fa.STEPS)
        self.assertTrue(set(s.prompts) <= set(FaceSession.LABELS))
        self.assertGreater(s.expires_at, timezone.now())

    def test_nonce_is_stored_hashed_only(self):
        s, nonce = fa.start(self.u, "login")
        self.assertNotEqual(s.nonce_hash, nonce)
        self.assertEqual(s.nonce_hash, fa._h(nonce))

    def test_unknown_purpose(self):
        with self.assertRaises(ValidationError):
            fa.start(self.u, "hack")

    def test_new_start_expires_the_old_one(self):
        a, _ = fa.start(self.u, "login")
        fa.start(self.u, "login")
        a.refresh_from_db()
        self.assertEqual(a.status, "expired")

    def test_daily_limit(self):
        for _ in range(fa.DAILY_LIMIT):
            fa.start(self.u, "login")
        with self.assertRaises(ValidationError):
            fa.start(self.u, "login")

    def test_daily_limit_is_per_user(self):
        for _ in range(fa.DAILY_LIMIT):
            fa.start(self.u, "login")
        fa.start(make_user("other"), "login")                        # must not raise

    def test_upgrade_requires_verified_id(self):
        with mock.patch.object(fa, "verified_types", return_value=set()):
            with self.assertRaises(ValidationError):
                fa.start(self.u, "upgrade")
        with mock.patch.object(fa, "verified_types", return_value={"bvn"}):
            fa.start(self.u, "upgrade")

    def test_bound_value_is_hashed(self):
        s, _ = fa.start(self.u, "phone_change", bound_to="08011111111")
        self.assertEqual(s.bound_hash, fa._h("08011111111"))
        self.assertNotIn("0801", s.bound_hash)
