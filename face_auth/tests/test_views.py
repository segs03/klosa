import io
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .. import service as fa
from ..models import FaceConsent, FaceSession
from .helpers import SETTINGS, FakeStorage, jpeg, make_user, passed, session


@override_settings(**SETTINGS)
class ViewTests(TestCase):
    def setUp(self):
        self.u = make_user("viewer")
        self.store = FakeStorage()
        p = mock.patch.object(fa, "verified_types", return_value={"nin"})
        p.start()
        self.addCleanup(p.stop)

    def set_pending(self, purpose="login", age=0):
        s = self.client.session
        s["face_pending"] = {"uid": self.u.pk, "purpose": purpose,
                             "backend": "django.contrib.auth.backends.ModelBackend",
                             "ts": timezone.now().timestamp() - age}
        s.save()

    def test_begin_without_user_or_pending_goes_to_login(self):
        self.assertRedirects(self.client.get(reverse("face_auth:begin")), reverse("accounts:login"),
                             fetch_redirect_response=False)

    def test_expired_pending_login_is_refused(self):
        self.set_pending(age=601)
        r = self.client.get(reverse("face_auth:begin"))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(FaceSession.objects.exists())

    def test_consent_required_and_recorded(self):
        self.set_pending()
        self.client.post(reverse("face_auth:begin"), {})
        self.assertFalse(FaceConsent.objects.exists())
        self.assertFalse(FaceSession.objects.exists())
        self.client.post(reverse("face_auth:begin"), {"consent": "on"})
        self.assertEqual(FaceConsent.objects.filter(user=self.u, version="1").count(), 1)
        self.client.post(reverse("face_auth:begin"), {"consent": "on"})   # again: still one record
        self.assertEqual(FaceConsent.objects.filter(user=self.u).count(), 1)

    def test_logged_in_user_cannot_start_arbitrary_purpose(self):
        self.client.force_login(self.u)
        r = self.client.post(reverse("face_auth:begin", args=["recovery"]), {"consent": "on"})
        self.assertEqual(r.status_code, 403)

    def test_frame_requires_actor(self):
        r = self.client.post(reverse("face_auth:frame"), {"step": 0})
        self.assertEqual(r.status_code, 403)

    def test_frame_without_started_check_or_file(self):
        self.client.force_login(self.u)
        r = self.client.post(reverse("face_auth:frame"), {"step": 0})
        self.assertEqual(r.status_code, 400)

    def test_frame_bad_step_value(self):
        self.client.force_login(self.u)
        s, n = fa.start(self.u, "upgrade")
        sess = self.client.session
        sess["face"] = {"id": s.pk, "nonce": n}
        sess.save()
        with mock.patch.object(fa, "storage", self.store):
            r = self.client.post(reverse("face_auth:frame"),
                                 {"step": "abc", "image": io.BytesIO(jpeg(1))})
        self.assertEqual(r.status_code, 400)

    def test_frame_wrong_nonce_is_403(self):
        self.client.force_login(self.u)
        s, _ = fa.start(self.u, "upgrade")
        sess = self.client.session
        sess["face"] = {"id": s.pk, "nonce": "forged"}
        sess.save()
        with mock.patch.object(fa, "storage", self.store):
            r = self.client.post(reverse("face_auth:frame"), {"step": 0, "image": io.BytesIO(jpeg(1))})
        self.assertEqual(r.status_code, 403)

    def test_result_logs_in_pending_user_once(self):
        passed(self.u, "login")
        self.set_pending("login")
        s = self.client.session
        s["face"] = {"id": FaceSession.objects.get().pk, "nonce": "n"}
        s.save()
        r = self.client.get(reverse("face_auth:result"))
        self.assertRedirects(r, reverse("accounts:home"), fetch_redirect_response=False)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertIsNotNone(FaceSession.objects.get().consumed_at)

    def test_result_does_not_log_in_failed_check(self):
        session(self.u, "login", status="failed", decided_at=timezone.now())
        self.set_pending("login")
        s = self.client.session
        s["face"] = {"id": FaceSession.objects.get().pk, "nonce": "n"}
        s.save()
        self.client.get(reverse("face_auth:result"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_result_cannot_use_another_users_session(self):
        victim = make_user("victim2")
        v = passed(victim, "login")
        self.set_pending("login")
        s = self.client.session
        s["face"] = {"id": v.pk, "nonce": "n"}
        s.save()
        self.client.get(reverse("face_auth:result"))
        self.assertNotIn("_auth_user_id", self.client.session)
