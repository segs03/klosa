import io
from datetime import timedelta
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from .. import service as fa
from ..models import FaceFrame, FaceSession
from .helpers import SETTINGS, FakeStorage, gradient, jpeg, make_user


@override_settings(**SETTINGS)
class AddFrameTests(TestCase):
    def setUp(self):
        self.u = make_user("frames")
        self.store = FakeStorage()
        for p in (mock.patch.object(fa, "storage", self.store),
                  mock.patch.object(fa, "PrivateStorage", return_value=self.store),
                  mock.patch.object(fa, "verified_types", return_value={"nin"})):
            p.start()
            self.addCleanup(p.stop)
        self.s, self.nonce = fa.start(self.u, "login")

    def add(self, step, data, nonce=None, user=None):
        return fa.add_frame(user or self.u, self.s.pk, self.nonce if nonce is None else nonce, step, data)

    def age_frames(self):
        FaceFrame.objects.update(taken_at=timezone.now() - timedelta(seconds=5))

    def test_three_frames_complete_and_trigger_finish(self):
        with mock.patch.object(fa, "finish") as fin:
            self.assertFalse(self.add(0, jpeg(1)))
            self.age_frames()
            self.assertFalse(self.add(1, jpeg(2)))
            self.age_frames()
            self.assertTrue(self.add(2, jpeg(3)))
        fin.assert_called_once_with(self.s.pk)
        self.assertEqual(self.s.frames.count(), 3)

    def test_wrong_nonce(self):
        with self.assertRaises(PermissionDenied):
            self.add(0, jpeg(1), nonce="wrong")
        with self.assertRaises(PermissionDenied):
            self.add(0, jpeg(1), nonce="")
        self.assertEqual(FaceFrame.objects.count(), 0)
        self.assertEqual(self.store.files, {})

    def test_other_users_session(self):
        with self.assertRaises(FaceSession.DoesNotExist):
            self.add(0, jpeg(1), user=make_user("thief"))

    def test_garbage_and_wrong_formats(self):
        png = io.BytesIO()
        Image.new("RGB", (300, 300), "red").save(png, "PNG")
        for bad in (b"", b"hello", b"\xff\xd8\xff" + b"junk" * 400, png.getvalue(), b"\x00" * 5000):
            with self.assertRaises(ValidationError):
                self.add(0, bad)
        self.assertEqual(FaceFrame.objects.count(), 0)

    def test_too_small_too_big_and_blank(self):
        with self.assertRaises(ValidationError):
            self.add(0, jpeg(1, size=100))                           # under MIN_SIDE
        with self.assertRaises(ValidationError):
            self.add(0, jpeg(1, size=900, quality=95))               # over MAX_BYTES
        blank = io.BytesIO()
        Image.new("RGB", (300, 300), (5, 5, 5)).save(blank, "JPEG", quality=95)
        with self.assertRaises(ValidationError):
            self.add(0, blank.getvalue())                            # dark / no detail

    def test_bad_step_numbers(self):
        for step in (-1, 3, 99):
            with self.assertRaises(ValidationError):
                self.add(step, jpeg(1))

    def test_cannot_skip_ahead(self):
        with self.assertRaises(ValidationError):
            self.add(1, jpeg(1))

    def test_retry_of_same_photo_is_idempotent(self):
        self.add(0, jpeg(1))
        self.add(0, jpeg(1))                                         # browser resend
        self.assertEqual(self.s.frames.count(), 1)

    def test_replacing_a_captured_step_is_rejected(self):
        self.add(0, jpeg(1))
        with self.assertRaises(ValidationError):
            self.add(0, jpeg(2))
        self.assertEqual(self.s.frames.get(step=0).sha256, FaceFrame.objects.get().sha256)

    def test_expired_session(self):
        FaceSession.objects.filter(pk=self.s.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(ValidationError):
            self.add(0, jpeg(1))

    def test_finished_session_rejects_more(self):
        FaceSession.objects.filter(pk=self.s.pk).update(status="passed")
        with self.assertRaises(ValidationError):
            self.add(0, jpeg(1))

    def test_too_fast(self):
        self.add(0, jpeg(1))
        with self.assertRaises(ValidationError):
            self.add(1, jpeg(2))                                     # no pause between frames

    def test_same_scene_twice_is_rejected(self):
        self.add(0, gradient(95))
        self.age_frames()
        with self.assertRaises(ValidationError):
            self.add(1, gradient(40))                                # different bytes, same picture

    def test_photo_reuse_across_sessions_is_rejected_and_cleaned_up(self):
        self.add(0, jpeg(7))
        s2, n2 = fa.start(self.u, "login")
        before = set(self.store.files)
        with self.assertRaises(ValidationError):
            fa.add_frame(self.u, s2.pk, n2, 0, jpeg(7))
        self.assertEqual(set(self.store.files), before)              # orphan file removed
