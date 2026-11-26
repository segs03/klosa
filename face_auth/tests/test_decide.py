from unittest import mock

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings

from .. import service as fa
from .helpers import SETTINGS, make_account, make_user, session


@override_settings(**SETTINGS)
class DecideTests(TestCase):
    def setUp(self):
        self.cust = make_user("cust")
        self.rev = make_user("rev", is_staff=True)
        self.rev.user_permissions.add(Permission.objects.get(codename="review_facesession"))
        self.s = session(self.cust, "upgrade", status="review")
        for p in (mock.patch.object(fa, "audit_log"),):
            p.start()
            self.addCleanup(p.stop)

    def test_must_be_staff_with_permission(self):
        for who in (make_user("plain"), make_user("staff_noperm", is_staff=True), self.cust):
            with self.assertRaises(PermissionDenied):
                fa.decide(self.s.pk, who, "passed")
        with self.assertRaises(PermissionDenied):
            fa.decide(self.s.pk, None, "passed")

    def test_inactive_reviewer_refused(self):
        self.rev.is_active = False
        self.rev.save()
        with self.assertRaises(PermissionDenied):
            fa.decide(self.s.pk, self.rev, "passed")

    def test_invalid_decision_and_missing_reason(self):
        with self.assertRaises(ValidationError):
            fa.decide(self.s.pk, self.rev, "maybe")
        with self.assertRaises(ValidationError):
            fa.decide(self.s.pk, self.rev, "failed", "   ")
        self.s.refresh_from_db()
        self.assertEqual(self.s.status, "review")

    def test_cannot_review_own_check(self):
        mine = session(self.rev, "upgrade", status="review")
        with self.assertRaises(PermissionDenied):
            fa.decide(mine.pk, self.rev, "passed")

    def test_only_review_status_can_be_decided_and_only_once(self):
        fa.decide(self.s.pk, self.rev, "failed", "photo unclear")
        with self.assertRaises(ValidationError):
            fa.decide(self.s.pk, self.rev, "passed")                 # cannot flip a final decision
        for st in ("capturing", "passed", "expired"):
            other = session(self.cust, "upgrade", status=st)
            with self.assertRaises(ValidationError):
                fa.decide(other.pk, self.rev, "passed")

    def test_pass_for_upgrade_updates_account_and_tier(self):
        make_account(self.cust)
        with mock.patch("accounts.tiers.refresh_tier") as rt:
            out = fa.decide(self.s.pk, self.rev, "passed")
        rt.assert_called_once()
        self.assertEqual((out.status, out.decided_by, out.reviewed_by), ("passed", "staff", self.rev))
        self.assertIsNotNone(out.decided_at)
        from accounts.models import Account
        self.assertIsNotNone(Account.objects.get(user=self.cust).face_verified_at)

    def test_pass_for_recovery_is_usable_and_leaves_tier_alone(self):
        s = session(self.cust, "recovery", status="review", bound_hash=fa._h(""))
        with mock.patch("accounts.tiers.refresh_tier") as rt:
            fa.decide(s.pk, self.rev, "passed")
        rt.assert_not_called()
        self.assertTrue(fa.consume(self.cust, "recovery"))

    def test_failed_does_not_grant_anything(self):
        make_account(self.cust)
        fa.decide(self.s.pk, self.rev, "failed", "does not match ID")
        from accounts.models import Account
        self.assertIsNone(Account.objects.get(user=self.cust).face_verified_at)
        self.assertFalse(fa.consume(self.cust, "upgrade"))
