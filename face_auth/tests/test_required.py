from unittest import mock

from django.test import TestCase, override_settings

from .. import service as fa
from .helpers import SETTINGS, enroll, make_user


@override_settings(**SETTINGS)
class RequiredTests(TestCase):
    def setUp(self):
        self.u = make_user("req")

    def test_purpose_not_enforced(self):
        with override_settings(FACE_ENFORCE=set()):
            for p in fa.MODES:
                self.assertFalse(fa.required(self.u, p))

    def test_login_only_when_opted_in(self):
        self.assertFalse(fa.required(self.u, "login"))               # no enrollment
        e = enroll(self.u)
        self.assertFalse(fa.required(self.u, "login"))               # enrolled but not opted in
        e.use_at_login = True
        e.save()
        self.assertTrue(fa.required(self.u, "login"))

    def test_ip_change_needs_enrollment_and_new_network(self):
        with mock.patch.object(fa, "new_network", return_value=True):
            self.assertFalse(fa.required(self.u, "ip_change"))       # not enrolled
            enroll(self.u)
            self.assertTrue(fa.required(self.u, "ip_change"))
        with mock.patch.object(fa, "new_network", return_value=False):
            self.assertFalse(fa.required(self.u, "ip_change"))

    def test_sensitive_actions_always_required(self):
        for p in ("signup", "upgrade", "recovery", "password_reset", "phone_change"):
            self.assertTrue(fa.required(self.u, p), p)
