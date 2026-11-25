import shutil
import tempfile
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.kyc_services import duplicate_hints, id_digest, review_application, submit_application
from accounts.models import Account, KYCApplication, KYCReview, VerifiedIdentity

User = get_user_model()
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
_n = iter(range(1, 10**6))


def upload(name="id.jpg", data=JPG):
    return SimpleUploadedFile(name, data, content_type="image/jpeg")


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        ctx = override_settings(PRIVATE_MEDIA_ROOT=self.tmp)
        ctx.enable()
        self.addCleanup(ctx.disable)
        self.user = User.objects.create_user("ann", password="pass12345")
        self.acc = Account.objects.create(user=self.user, account_number="1000000001")

    def customer(self, name="bob"):
        u = User.objects.create_user(name, password="pass12345")
        Account.objects.create(user=u, account_number=str(2000000000 + next(_n)))
        return u

    def staff(self, perm=True):
        s = User.objects.create_user(f"s{next(_n)}", password="pass12345", is_staff=True)
        if perm:
            s.user_permissions.add(Permission.objects.get(codename="review_kycapplication"))
        return User.objects.get(pk=s.pk)

    def submit(self, user=None, **kw):
        args = dict(doc_type="nin", id_number="12345678901", legal_name="Ada Chinwe Obi",
                    date_of_birth=date(1990, 1, 1), document=upload())
        args.update(kw)
        return submit_application(user or self.user, **args)


class SubmitTests(Base):
    def test_a_request_keeps_a_fingerprint_not_the_number(self):
        a = self.submit()
        self.assertEqual(a.status, "pending")
        self.assertEqual(a.identity_digest, id_digest("nin", "12345678901"))
        self.assertNotIn("12345678901", a.identity_digest)
        self.assertEqual(len(a.identity_digest), 64)

    def test_spaces_and_dashes_make_the_same_fingerprint(self):
        a = self.submit(id_number="1234 5678-901")
        self.assertEqual(a.identity_digest, id_digest("nin", "12345678901"))

    def test_bad_input_is_refused(self):
        bad = [{"doc_type": "x"}, {"id_number": "123"}, {"id_number": "1234567890a"}, {"legal_name": "A"},
               {"legal_name": "Ada123"}, {"date_of_birth": date.today()},
               {"date_of_birth": date.today() - timedelta(days=365 * 17)}, {"date_of_birth": date(1850, 1, 1)},
               {"date_of_birth": date.today() + timedelta(days=5)}]
        for kw in bad:
            with self.assertRaises(ValidationError, msg=str(kw)):
                self.submit(**kw)
        self.assertEqual(KYCApplication.objects.count(), 0)

    def test_one_open_request_at_a_time(self):
        self.submit()
        with self.assertRaises(ValidationError):
            self.submit()

    def test_answering_a_request_for_more_information_replaces_the_request(self):
        a = self.submit()
        KYCApplication.objects.filter(pk=a.pk).update(status="additional_info")
        b = self.submit(legal_name="Ada Obi", document=upload("new.jpg"))
        self.assertEqual((b.pk, b.status, b.legal_name), (a.pk, "pending", "Ada Obi"))
        self.assertEqual(KYCApplication.objects.count(), 1)

    def test_three_submissions_in_30_days_is_the_limit(self):
        for _ in range(3):
            a = self.submit()
            KYCApplication.objects.filter(pk=a.pk).update(status="rejected")
        with self.assertRaises(ValidationError):
            self.submit()

    def test_a_verified_or_closed_account_cannot_submit(self):
        a = self.submit()
        review_application(a.pk, self.staff(), "approved", "Looks right")
        with self.assertRaises(ValidationError):
            self.submit()
        other = self.customer()
        Account.objects.filter(user=other).update(closed_at=timezone.now())
        with self.assertRaises(ValidationError):
            self.submit(other, id_number="99999999999")


class ReviewTests(Base):
    def test_approval_verifies_raises_the_tier_and_fills_in_the_name(self):
        a = self.submit()
        review_application(a.pk, self.staff(), "approved", "Matches the ID")
        a.refresh_from_db()
        self.acc.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual((a.status, self.acc.kyc_tier), ("verified", 0))   # a NIN alone no longer upgrades
        self.assertTrue(VerifiedIdentity.objects.filter(user=self.user, identity_digest=a.identity_digest).exists())
        self.assertEqual((self.user.first_name, self.user.last_name), ("Ada", "Chinwe Obi"))

    def test_a_bvn_approval_raises_the_tier(self):
        app = KYCApplication.objects.create(
            user=self.user, country_code="NG", document_type="bvn", legal_name="Ada Obi",
            date_of_birth=date(1990, 1, 1), identity_document="kyc/x.jpg",
            identity_digest=id_digest("bvn", "12345678901"))
        review_application(app.pk, self.staff(), "approved", "ok")
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.kyc_tier, 1)

    def test_rejection_and_requests_for_more_information(self):
        a = self.submit()
        review_application(a.pk, self.staff(), "additional_info", "Photo is blurry")
        a.refresh_from_db()
        self.assertEqual(a.status, "additional_info")
        b = self.customer()
        c = self.submit(b, id_number="22222222222")
        review_application(c.pk, self.staff(), "rejected", "Name does not match")
        c.refresh_from_db()
        self.assertEqual(c.status, "rejected")
        self.assertFalse(VerifiedIdentity.objects.exists())

    def test_only_staff_with_the_permission_may_review(self):
        a = self.submit()
        for who in (self.staff(perm=False), self.customer("cat")):
            with self.assertRaises(PermissionDenied):
                review_application(a.pk, who, "approved", "ok")

    def test_you_cannot_review_your_own_request(self):
        a = self.submit()
        s = self.staff()
        KYCApplication.objects.filter(pk=a.pk).update(user=s)
        Account.objects.create(user=s, account_number="3000000001")
        with self.assertRaises(PermissionDenied):
            review_application(a.pk, s, "approved", "ok")

    def test_a_reason_is_required_and_a_reviewer_cannot_decide_twice(self):
        a = self.submit()
        s = self.staff()
        for reason in ("", "  ", "x" * 501):
            with self.assertRaises(ValidationError):
                review_application(a.pk, s, "approved", reason)
        with self.assertRaises(ValidationError):
            review_application(a.pk, s, "maybe", "ok")
        with override_settings(KYC_APPROVALS_REQUIRED=2):
            review_application(a.pk, s, "approved", "first")
            with self.assertRaises(ValidationError):
                review_application(a.pk, s, "approved", "again")


    @override_settings(KYC_APPROVALS_REQUIRED=2)
    def test_two_person_approval(self):
        a = self.submit()
        review_application(a.pk, self.staff(), "approved", "one")
        a.refresh_from_db()
        self.assertEqual(a.status, "under_review")
        self.assertFalse(VerifiedIdentity.objects.exists())
        review_application(a.pk, self.staff(), "approved", "two")
        a.refresh_from_db()
        self.assertEqual(a.status, "verified")

    def test_an_id_already_used_by_another_account_cannot_be_approved(self):
        first = self.submit()
        review_application(first.pk, self.staff(), "approved", "ok")
        other = self.customer()
        second = self.submit(other)                              # accepted, so nobody can probe which IDs exist
        self.assertTrue(duplicate_hints(second)["same_id"])
        with self.assertRaises(ValidationError):
            review_application(second.pk, self.staff(), "approved", "ok")
        second.refresh_from_db()
        self.assertEqual(second.status, "pending")
        self.assertEqual(KYCReview.objects.filter(application=second).count(), 0)

    def test_an_applicant_who_turned_out_to_be_under_18_cannot_be_approved(self):
        a = self.submit()
        KYCApplication.objects.filter(pk=a.pk).update(date_of_birth=date.today() - timedelta(days=365 * 10))
        with self.assertRaises(ValidationError):
            review_application(a.pk, self.staff(), "approved", "ok")

    def test_the_same_name_and_birthday_is_flagged_for_the_reviewer(self):
        a = self.submit()
        review_application(a.pk, self.staff(), "approved", "ok")
        b = self.submit(self.customer(), id_number="55555555555", legal_name="ADA CHINWE OBI")
        self.assertEqual(duplicate_hints(b)["same_name_dob"], 1)


class ViewTests(Base):
    def setUp(self):
        super().setUp()
        self.client.login(username="ann", password="pass12345")
        self.url = reverse("accounts:verify")

    def post(self, **kw):
        data = {"doc_type": "nin", "id_number": "12345678901", "legal_name": "Ada Obi",
                "date_of_birth": "1990-01-01", "document": upload(), "consent": "on"}
        data.update(kw)
        return self.client.post(self.url, data)

    def test_a_good_submission_is_stored_under_a_random_name(self):
        r = self.post(document=upload("my passport.jpg"))
        self.assertRedirects(r, self.url)
        a = KYCApplication.objects.get()
        self.assertRegex(a.identity_document.name, r"^kyc/[0-9a-f]{32}\.jpg$")
        self.assertContains(self.client.get(self.url), "reviewing")

    def test_fake_files_big_files_and_a_missing_consent_are_refused(self):
        self.post(document=upload("id.jpg", b"this is text, not an image"))
        self.post(consent="")
        with override_settings(KYC_MAX_UPLOAD=100):
            self.post()
        self.assertEqual(KYCApplication.objects.count(), 0)

    def test_the_reviewer_message_is_shown_when_more_information_is_needed(self):
        a = self.submit()
        review_application(a.pk, self.staff(), "additional_info", "Photo is blurry")
        KYCApplication.objects.filter(pk=a.pk).update(status="additional_info")
        self.assertContains(self.client.get(self.url), "Photo is blurry")

    def test_anonymous_users_are_sent_to_login(self):
        self.assertEqual(Client().get(self.url).status_code, 302)

    def test_staff_pages_and_documents_are_private(self):
        a = self.submit()
        urls = [reverse("accounts:kyc_staff"), reverse("accounts:kyc_review", args=[a.pk]),
                reverse("accounts:kyc_document", args=[a.pk])]
        for u in urls:
            self.assertEqual(self.client.get(u).status_code, 403)       # a customer
            self.assertEqual(Client().get(u).status_code, 302)          # anonymous
        s = self.staff()
        c = Client()
        c.login(username=s.username, password="pass12345")
        self.assertEqual(c.get(urls[0]).status_code, 200)
        self.assertEqual(c.get(urls[1]).status_code, 200)
        r = c.get(urls[2])
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r["Content-Disposition"].startswith("attachment"))
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertIn("no-store", r["Cache-Control"])
        c2 = Client()
        no_perm = self.staff(perm=False)
        c2.login(username=no_perm.username, password="pass12345")
        self.assertEqual(c2.get(urls[2]).status_code, 403)

    def test_a_staff_member_can_decide_through_the_screen(self):
        a = self.submit()
        s = self.staff()
        c = Client()
        c.login(username=s.username, password="pass12345")
        c.post(reverse("accounts:kyc_review", args=[a.pk]), {"decision": "approved", "reason": "Good"})
        a.refresh_from_db()
        self.assertEqual(a.status, "verified")
        


class NumberFormatTests(Base):
    def msg(self, **kw):
        with self.assertRaises(ValidationError) as cm:
            self.submit(**kw)
        return cm.exception.messages[0]

    def test_a_short_nin_says_how_many_digits_were_entered(self):
        m = self.msg(id_number="123456789")
        self.assertIn("exactly 11 digits", m)
        self.assertIn("9", m)

    def test_letters_in_a_nin_are_explained(self):
        self.assertIn("digits only", self.msg(id_number="1234567890a"))

    def test_spaces_dashes_and_hidden_characters_are_ignored(self):
        a = self.submit(id_number="123\u200b4 5678-901")
        self.assertEqual(a.identity_digest, id_digest("nin", "12345678901"))

    def test_a_bvn_follows_the_same_rule(self):
        self.assertIn("number only", self.msg(doc_type="bvn", id_number="12"))

    def test_other_documents_keep_their_own_rules(self):
        self.assertEqual(self.submit(doc_type="passport", id_number="a12345678").document_type, "passport")
        self.assertIn("passport", self.msg(doc_type="passport", id_number="12").lower())

    def test_an_empty_number_is_asked_for(self):
        self.assertIn("Enter your ID number", self.msg(id_number="   "))