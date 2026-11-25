import io
import shutil
import tempfile
from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import face
from accounts.models import Account, FaceCheck, FaceFrame, KYCApplication, VerifiedDocument

User = get_user_model()
try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None


class AutoPass:
    def assess(self, check):
        return "passed", 0.99, "ref-1"


def jpeg(kind, quality=70):
    base = Image.linear_gradient("L").resize((320, 320)).convert("RGB")
    img = {0: base, 1: ImageOps.invert(base), 2: base.transpose(Image.TRANSPOSE)}[kind]
    out = io.BytesIO()
    img.save(out, "JPEG", quality=quality)
    return out.getvalue()


def upload(data):
    return SimpleUploadedFile("f.jpg", data, content_type="image/jpeg")


def staff(*codes):
    s = User.objects.create_user(f"s{User.objects.count()}", password="pass12345", is_staff=True)
    s.user_permissions.add(*Permission.objects.filter(codename__in=codes))
    return User.objects.get(pk=s.pk)



class FaceTests(TestCase):
    def setUp(self):
        if Image is None:
            self.skipTest("Pillow is not installed")
        cache.clear()
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ctx = override_settings(PRIVATE_MEDIA_ROOT=tmp)
        ctx.enable()
        self.addCleanup(ctx.disable)
        gap = mock.patch("accounts.face.MIN_GAP", 0)
        gap.start()
        self.addCleanup(gap.stop)
        self.u = User.objects.create_user("ann", password="pass12345")
        self.acc = Account.objects.create(user=self.u, account_number="1000000001")
        app = KYCApplication.objects.create(user=self.u, country_code="NG", document_type="nin", legal_name="Ada Obi",
                                            date_of_birth=date(1990, 1, 1), identity_document="kyc/x.jpg",
                                            identity_digest="a" * 64, status="verified")
        VerifiedDocument.objects.create(user=self.u, document_type="nin", identity_digest="a" * 64,
                                        verified_at=timezone.now(), source_application=app)
        self.c = Client()
        self.c.login(username="ann", password="pass12345")

    def begin(self, c=None):
        return (c or self.c).post(reverse("accounts:face_start"), {"consent": "on"})

    def send(self, step, data, c=None):
        return (c or self.c).post(reverse("accounts:face_frame"), {"step": step, "image": upload(data)})

    def complete(self):
        self.begin()
        for i in range(3):
            self.send(i, jpeg(i))
        return FaceCheck.objects.get()

    def test_it_needs_a_verified_id_and_consent(self):
        FaceCheck.objects.all().delete()
        VerifiedDocument.objects.all().delete()
        self.begin()
        self.assertFalse(FaceCheck.objects.exists())
        VerifiedDocument.objects.create(user=self.u, document_type="nin", identity_digest="b" * 64,
                                        verified_at=timezone.now(), source_application=KYCApplication.objects.get())
        self.c.post(reverse("accounts:face_start"), {})
        self.assertFalse(FaceCheck.objects.exists())

    def test_three_frames_go_to_review_and_never_pass_by_themselves(self):
        check = self.complete()
        self.assertEqual(check.status, "review")
        self.assertEqual(check.frames.count(), 3)
        self.acc.refresh_from_db()
        self.assertIsNone(self.acc.face_verified_at)
        self.assertEqual(len(set(check.prompts)), 3)

    def test_the_same_photo_cannot_be_reused_or_held_up_twice(self):
        self.begin()
        self.assertEqual(self.send(0, jpeg(0)).status_code, 200)
        self.assertEqual(self.send(1, jpeg(0)).status_code, 400)             # the identical photo
        self.assertEqual(self.send(1, jpeg(0, quality=75)).status_code, 400)  # the same scene again
        self.assertEqual(self.send(1, jpeg(1)).status_code, 200)

    def test_a_photo_used_by_another_account_is_refused(self):
        self.complete()
        other = User.objects.create_user("bob", password="pass12345")
        oacc = Account.objects.create(user=other, account_number="1000000002")
        VerifiedDocument.objects.create(user=other, document_type="nin", identity_digest="c" * 64, verified_at=timezone.now(),
                                        source_application=KYCApplication.objects.create(
                                            user=other, country_code="NG", document_type="nin", legal_name="B", date_of_birth=date(1990, 1, 1),
                                            identity_document="kyc/y.jpg", identity_digest="c" * 64, status="verified"))
        c2 = Client()
        c2.login(username="bob", password="pass12345")
        self.begin(c2)
        self.assertEqual(self.send(0, jpeg(0), c2).status_code, 400)

    def test_frames_must_come_in_order_and_not_too_fast(self):
        self.begin()
        self.assertEqual(self.send(1, jpeg(1)).status_code, 400)
        self.assertEqual(self.send(0, jpeg(0)).status_code, 200)
        with mock.patch("accounts.face.MIN_GAP", 60):
            self.assertEqual(self.send(1, jpeg(1)).status_code, 400)

    def test_bad_files_and_missing_sessions_are_refused(self):
        self.assertEqual(self.send(0, jpeg(0)).status_code, 400)             # no check started
        self.begin()
        for data in (b"not an image" * 200, b"\xff\xd8\xff" + b"0" * 5000, b""):
            self.assertEqual(self.send(0, data).status_code, 400)

    def test_an_expired_check_is_refused(self):
        self.begin()
        FaceCheck.objects.update(expires_at=timezone.now() - timezone.timedelta(minutes=1))
        self.assertEqual(self.send(0, jpeg(0)).status_code, 400)

    def test_only_three_attempts_a_day(self):
        for _ in range(4):
            self.begin()
        self.assertEqual(FaceCheck.objects.count(), 3)

    def test_a_second_person_decides_and_it_unlocks_the_flag(self):
        check = self.complete()
        s = Client()
        rev = staff("review_facecheck")
        s.login(username=rev.username, password="pass12345")
        s.post(reverse("accounts:face_review", args=[check.pk]), {"decision": "failed", "reason": ""})
        check.refresh_from_db()
        self.assertEqual(check.status, "review")                              # a fail needs a reason
        s.post(reverse("accounts:face_review", args=[check.pk]), {"decision": "passed"})
        check.refresh_from_db()
        self.acc.refresh_from_db()
        self.assertEqual(check.status, "passed")
        self.assertIsNotNone(self.acc.face_verified_at)

    def test_you_cannot_review_your_own_check_and_customers_cannot_open_staff_pages(self):
        check = self.complete()
        for url in (reverse("accounts:face_queue"), reverse("accounts:face_review", args=[check.pk]),
                    reverse("accounts:face_image", args=[check.pk, 0])):
            self.assertEqual(self.c.get(url).status_code, 403)
            self.assertEqual(Client().get(url).status_code, 302)
        self.u.is_staff = True
        self.u.save()
        self.u.user_permissions.add(Permission.objects.get(codename="review_facecheck"))
        c = Client()
        c.login(username="ann", password="pass12345")
        c.post(reverse("accounts:face_review", args=[check.pk]), {"decision": "passed"})
        check.refresh_from_db()
        self.assertEqual(check.status, "review")

    def test_images_are_served_only_to_reviewers_with_safe_headers(self):
        check = self.complete()
        rev = staff("review_facecheck")
        s = Client()
        s.login(username=rev.username, password="pass12345")
        r = s.get(reverse("accounts:face_image", args=[check.pk, 0]))
        self.assertEqual((r.status_code, r["Content-Type"], r["X-Content-Type-Options"]), (200, "image/jpeg", "nosniff"))
        self.assertIn("no-store", r["Cache-Control"])

    @override_settings(FACE_PROVIDER="accounts.tests.test_face.AutoPass")
    def test_a_provider_can_pass_a_check_automatically(self):
        self.complete()
        self.acc.refresh_from_db()
        self.assertIsNotNone(self.acc.face_verified_at)
        
        
    def test_a_retried_frame_is_answered_the_same_way(self):
        self.begin()
        data = jpeg(0)
        self.assertEqual(self.send(0, data).status_code, 200)
        self.assertEqual(self.send(0, data).status_code, 200)            # the browser retried
        self.assertEqual(FaceFrame.objects.count(), 1)
        self.assertEqual(self.send(0, jpeg(1)).status_code, 400)         # another photo for a used step is refused