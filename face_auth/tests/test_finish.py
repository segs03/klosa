from unittest import mock

import numpy as np
from django.test import TestCase, override_settings

from .. import engine, service as fa, vault
from ..models import FaceDuplicate, FaceEnrollment
from .helpers import SETTINGS, enroll, make_account, make_user, session, vec


@override_settings(**SETTINGS)
class FinishTests(TestCase):
    def setUp(self):
        self.u = make_user("fin")
        make_account(self.u)
        p1 = mock.patch.object(fa, "_read_frames", return_value=[b"a", b"b", b"c"])
        p2 = mock.patch.object(fa, "_welcome")
        p3 = mock.patch.object(engine, "available", return_value=True)
        for p in (p1, p2, p3):
            p.start()
            self.addCleanup(p.stop)

    def run_finish(self, s, result=None, exc=None):
        with mock.patch.object(engine, "analyse", side_effect=exc, return_value=result):
            return fa.finish(s.pk)

    def test_non_capturing_session_is_untouched(self):
        s = session(self.u, "login", status="passed")
        with mock.patch.object(engine, "analyse") as an:
            fa.finish(s.pk)
        an.assert_not_called()

    def test_engine_unavailable_auto_fails_closed(self):
        enroll(self.u)
        s = session(self.u, "login")
        with mock.patch.object(engine, "available", return_value=False):
            out = fa.finish(s.pk)
        self.assertEqual(out.status, "failed")                      # never auto-pass when the engine is down

    def test_engine_unavailable_review_goes_to_staff(self):
        s = session(self.u, "upgrade")
        with mock.patch.object(engine, "available", return_value=False):
            self.assertEqual(fa.finish(s.pk).status, "review")

    def test_engine_crash_auto_fails_closed(self):
        enroll(self.u)
        s = session(self.u, "login")
        self.assertEqual(self.run_finish(s, exc=RuntimeError("boom")).status, "failed")

    def test_engine_crash_review_goes_to_staff(self):
        s = session(self.u, "upgrade")
        self.assertEqual(self.run_finish(s, exc=RuntimeError("boom")).status, "review")

    def test_every_failure_code_fails_with_message(self):
        for code in ("no_face", "many_faces", "unreadable", "prompt"):
            s = session(self.u, "signup")
            out = self.run_finish(s, (None, 0.0, code))
            self.assertEqual(out.status, "failed", code)
            self.assertIn(fa.TEXT[code], out.reason)
        self.assertFalse(FaceEnrollment.objects.filter(user=self.u).exists())

    def test_frames_of_different_people_fail(self):
        s = session(self.u, "signup")
        out = self.run_finish(s, (vec(1), fa.FRAMES_MATCH - 0.01, "ok"))
        self.assertEqual(out.status, "failed")
        self.assertFalse(FaceEnrollment.objects.filter(user=self.u).exists())

    def test_enroll_success_stores_encrypted_template(self):
        s = session(self.u, "signup")
        out = self.run_finish(s, (vec(1), 0.9, "ok"))
        self.assertEqual(out.status, "passed")
        e = FaceEnrollment.objects.get(user=self.u)
        self.assertNotEqual(bytes(e.template_enc), vec(1))           # not stored in the clear
        self.assertEqual(vault.unseal(e.template_enc), vec(1))

    def test_enroll_twice_is_refused_and_keeps_original(self):
        enroll(self.u, vec(1))
        s = session(self.u, "signup")
        out = self.run_finish(s, (vec(0, 1), 0.9, "ok"))
        self.assertEqual(out.status, "failed")
        self.assertEqual(vault.unseal(FaceEnrollment.objects.get(user=self.u).template_enc), vec(1))

    def test_auto_match_passes(self):
        enroll(self.u, vec(1))
        self.assertEqual(self.run_finish(session(self.u, "login"), (vec(1), 0.9, "ok")).status, "passed")

    def test_auto_stranger_fails(self):
        enroll(self.u, vec(1))
        out = self.run_finish(session(self.u, "login"), (vec(0, 1), 0.9, "ok"))   # orthogonal = different face
        self.assertEqual(out.status, "failed")
        self.assertEqual(out.decided_by, "auto")

    def test_auto_threshold_boundary(self):
        enroll(self.u, vec(1))
        at = lambda c: vec(c, float(np.sqrt(1 - c * c)))     # unit vector with cosine c
        below = self.run_finish(session(self.u, "login"), (at(0.39), 0.9, "ok"))
        above = self.run_finish(session(self.u, "login"), (at(0.41), 0.9, "ok"))
        self.assertEqual((below.status, above.status), ("failed", "passed"))
        
    def test_auto_without_enrollment_falls_back_to_review(self):
        out = self.run_finish(session(self.u, "login"), (vec(1), 0.9, "ok"))
        self.assertEqual(out.status, "review")                       # never auto-pass without a template

    def test_recovery_and_upgrade_never_auto_pass(self):
        enroll(self.u, vec(1))
        for p in ("recovery", "upgrade"):
            out = self.run_finish(session(self.u, p), (vec(1), 0.9, "ok"))   # perfect match
            self.assertEqual(out.status, "review", p)
            self.assertEqual(out.decided_by, "none")

    def test_duplicate_face_is_flagged(self):
        other = make_user("twin")
        make_account(other)
        enroll(other, vec(1))
        self.run_finish(session(self.u, "signup"), (vec(1), 0.9, "ok"))
        self.assertTrue(FaceDuplicate.objects.filter(user=self.u, other_user=other, status="open").exists())
        self.assertFalse(fa.bonus_ok(self.u))

    def test_different_face_not_flagged(self):
        other = make_user("stranger")
        make_account(other)
        enroll(other, vec(0, 1))
        self.run_finish(session(self.u, "signup"), (vec(1), 0.9, "ok"))
        self.assertFalse(FaceDuplicate.objects.exists())
        self.assertTrue(fa.bonus_ok(self.u))

    def test_closed_accounts_are_not_matched(self):
        gone = make_user("closed")
        make_account(gone, closed=True)
        enroll(gone, vec(1))
        self.run_finish(session(self.u, "signup"), (vec(1), 0.9, "ok"))
        self.assertFalse(FaceDuplicate.objects.exists())

    def test_cleared_duplicate_allows_bonus(self):
        other = make_user("twin2")
        make_account(other)
        enroll(other, vec(1))
        self.run_finish(session(self.u, "signup"), (vec(1), 0.9, "ok"))
        FaceDuplicate.objects.update(status="cleared")
        self.assertTrue(fa.bonus_ok(self.u))

    def test_bonus_needs_enrollment(self):
        self.assertFalse(fa.bonus_ok(self.u))

    def test_welcome_runs_only_after_commit_and_only_on_enroll(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.run_finish(session(self.u, "signup"), (vec(1), 0.9, "ok"))
        fa._welcome.assert_called_once_with(self.u.pk)
