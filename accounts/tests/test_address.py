from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.address_forms import AddressForm
from accounts.address_services import review_address, submit_address
from accounts.models import Account, AddressProof

from .helpers import JPG, PASSWORD, make_account, make_staff, make_user, make_verified


def good(days_old=10, **over):
    data = dict(line1="12 Admiralty Way", line2="Lekki Phase 1", city="Lagos", state="Lagos",
                bill_date=timezone.localdate() - timedelta(days=days_old),
                document=SimpleUploadedFile("bill.jpg", JPG, "image/jpeg"))
    data.update(over)
    return data


class SubmitAddressTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.acc = make_account(self.user)

    def test_a_good_submission_waits_for_review(self):
        proof = submit_address(self.user, **good(line1="  12   Admiralty  Way "))
        self.assertEqual((proof.status, proof.line1, proof.city), ("pending", "12 Admiralty Way", "Lagos"))

    def test_the_bill_must_be_recent_but_not_from_the_future(self):
        with self.assertRaisesMessage(ValidationError, "future"):
            submit_address(self.user, **good(days_old=-1))
        with self.assertRaisesMessage(ValidationError, "last 3 months"):
            submit_address(self.user, **good(days_old=91))
        self.assertTrue(submit_address(self.user, **good(days_old=90)))      # exactly 90 days is fine

    def test_blank_or_tiny_fields_are_refused(self):
        for field, value in (("line1", "x"), ("line1", "   "), ("city", ""), ("state", "L")):
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                submit_address(self.user, **good(**{field: value}))
        self.assertFalse(AddressProof.objects.exists())

    def test_only_one_open_request_at_a_time(self):
        submit_address(self.user, **good())
        with self.assertRaisesMessage(ValidationError, "already being reviewed"):
            submit_address(self.user, **good())

    def test_a_verified_address_cannot_be_submitted_again(self):
        AddressProof.objects.create(user=self.user, line1="1 Road", city="Lagos", state="Lagos",
                                    bill_date=timezone.localdate(), document="a.jpg", status="approved")
        with self.assertRaisesMessage(ValidationError, "already verified"):
            submit_address(self.user, **good())

    def test_a_closed_account_is_refused(self):
        Account.objects.filter(pk=self.acc.pk).update(closed_at=timezone.now())
        with self.assertRaisesMessage(ValidationError, "not open"):
            submit_address(self.user, **good())

    def test_three_tries_in_30_days_is_the_limit(self):
        staff = make_staff("review_addressproof")
        for _ in range(3):
            proof = submit_address(self.user, **good())
            review_address(proof.pk, staff, "rejected", "Blurry photo")
        with self.assertRaisesMessage(ValidationError, "limit of 3"):
            submit_address(self.user, **good())


class ReviewAddressTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.acc = make_account(self.user)
        self.staff = make_staff("review_addressproof")
        self.proof = submit_address(self.user, **good())

    def test_approval_is_recorded(self):
        review_address(self.proof.pk, self.staff, "approved")
        self.proof.refresh_from_db()
        self.assertEqual((self.proof.status, self.proof.reviewed_by), ("approved", self.staff))
        self.assertIsNotNone(self.proof.reviewed_at)
        

    @override_settings(KYC_EMAIL_REQUIRED=False)
    def test_approval_raises_the_tier_when_everything_else_is_done(self):
        make_verified(self.user, "bvn")
        make_verified(self.user, "nin")
        Account.objects.filter(pk=self.acc.pk).update(face_verified_at=timezone.now(), kyc_tier=1)
        review_address(self.proof.pk, self.staff, "approved")
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.kyc_tier, 2)

    def test_approval_alone_does_not_raise_the_tier(self):
        review_address(self.proof.pk, self.staff, "approved")
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.kyc_tier, 0)

    def test_rejection_needs_a_reason_and_keeps_the_tier(self):
        with self.assertRaisesMessage(ValidationError, "Say why"):
            review_address(self.proof.pk, self.staff, "rejected", "  ")
        review_address(self.proof.pk, self.staff, "rejected", "Bill is too old")
        self.proof.refresh_from_db()
        self.assertEqual((self.proof.status, self.proof.reason), ("rejected", "Bill is too old"))

    def test_a_rejected_customer_can_try_again(self):
        review_address(self.proof.pk, self.staff, "rejected", "Blurry photo")
        self.assertEqual(submit_address(self.user, **good()).status, "pending")

    def test_nobody_reviews_their_own_address(self):
        staff_user = make_staff("review_addressproof")
        make_account(staff_user)
        mine = submit_address(staff_user, **good())
        with self.assertRaises(PermissionDenied):
            review_address(mine.pk, staff_user, "approved")

    def test_only_permitted_staff_can_review(self):
        plain_staff = make_user(is_staff=True)
        for who in (self.user, plain_staff, make_user()):
            with self.subTest(who=who.username), self.assertRaises(PermissionDenied):
                review_address(self.proof.pk, who, "approved")
        self.proof.refresh_from_db()
        self.assertEqual(self.proof.status, "pending")

    def test_bad_decisions_and_long_reasons_are_refused(self):
        with self.assertRaisesMessage(ValidationError, "Choose a decision"):
            review_address(self.proof.pk, self.staff, "maybe")
        with self.assertRaisesMessage(ValidationError, "500"):
            review_address(self.proof.pk, self.staff, "rejected", "x" * 501)

    def test_a_decided_request_cannot_be_decided_again(self):
        review_address(self.proof.pk, self.staff, "approved")
        with self.assertRaisesMessage(ValidationError, "not waiting"):
            review_address(self.proof.pk, self.staff, "rejected", "Changed my mind")


class AddressFormTests(TestCase):
    def data(self, **over):
        d = {"line1": "12 Admiralty Way", "city": "Lagos", "state": "Lagos",
             "bill_date": str(timezone.localdate()), "consent": "on"}
        d.update(over)
        return d

    def form(self, content=JPG, name="bill.jpg", **over):
        return AddressForm(self.data(**over), {"document": SimpleUploadedFile(name, content)})

    def test_a_picture_is_accepted_and_renamed(self):
        f = self.form()
        self.assertTrue(f.is_valid(), f.errors)
        self.assertNotIn("bill", f.cleaned_data["document"].name)
        self.assertTrue(f.cleaned_data["document"].name.endswith(".jpg"))

    def test_a_pdf_is_accepted(self):
        self.assertTrue(self.form(b"%PDF-1.7 data", "bill.pdf").is_valid())

    def test_a_fake_picture_is_refused(self):
        f = self.form(b"just some text", "bill.jpg")
        self.assertFalse(f.is_valid())
        self.assertIn("document", f.errors)

    @override_settings(KYC_MAX_UPLOAD=10)
    def test_a_big_file_is_refused(self):
        self.assertIn("document", self.form().errors)

    def test_consent_is_required(self):
        f = AddressForm({k: v for k, v in self.data().items() if k != "consent"},
                        {"document": SimpleUploadedFile("b.jpg", JPG)})
        self.assertIn("consent", f.errors)


class AddressViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("ann")
        self.acc = make_account(self.user)
        self.client.login(username="ann", password=PASSWORD)
        self.url = reverse("accounts:address")

    def post(self, **over):
        data = {"line1": "12 Admiralty Way", "city": "Lagos", "state": "Lagos",
                "bill_date": str(timezone.localdate() - timedelta(days=5)), "consent": "on",
                "document": SimpleUploadedFile("bill.jpg", JPG, "image/jpeg")}
        data.update(over)
        return self.client.post(self.url, data)

    def staff_client(self, codename="review_addressproof"):
        staff = make_staff(codename)
        c = Client()
        c.login(username=staff.username, password=PASSWORD)
        return c, staff

    def test_you_must_be_logged_in(self):
        self.assertEqual(Client().get(self.url).status_code, 302)

    def test_the_page_loads(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context["can_submit"])

    def test_a_good_post_creates_a_pending_request(self):
        r = self.post()
        self.assertRedirects(r, self.url, fetch_redirect_response=False)
        self.assertEqual(AddressProof.objects.get(user=self.user).status, "pending")
        self.assertFalse(self.client.get(self.url).context["can_submit"])

    def test_a_bad_post_creates_nothing_and_shows_the_error(self):
        r = self.post(bill_date=str(timezone.localdate() - timedelta(days=200)))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "last 3 months")
        self.assertFalse(AddressProof.objects.exists())

    def test_a_missing_consent_creates_nothing(self):
        r = self.post(consent="")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(AddressProof.objects.exists())

    def test_customers_cannot_open_the_staff_pages(self):
        proof = AddressProof.objects.create(user=self.user, line1="1 Road", city="Lagos", state="Lagos",
                                            bill_date=timezone.localdate(), document="a.jpg")
        for name, args in (("accounts:address_staff", []), ("accounts:address_review", [proof.pk]),
                           ("accounts:address_document", [proof.pk])):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403)

    def test_staff_see_the_queue(self):
        self.post()
        c, _ = self.staff_client()
        r = c.get(reverse("accounts:address_staff"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.context["proofs"]), 1)

    def test_staff_can_approve_from_the_review_page(self):
        self.post()
        proof = AddressProof.objects.get(user=self.user)
        c, staff = self.staff_client()
        self.assertEqual(c.get(reverse("accounts:address_review", args=[proof.pk])).status_code, 200)
        r = c.post(reverse("accounts:address_review", args=[proof.pk]), {"decision": "approved"})
        self.assertRedirects(r, reverse("accounts:address_staff"), fetch_redirect_response=False)
        proof.refresh_from_db()
        self.assertEqual((proof.status, proof.reviewed_by), ("approved", staff))

    def test_a_rejection_without_a_reason_stays_pending(self):
        self.post()
        proof = AddressProof.objects.get(user=self.user)
        c, _ = self.staff_client()
        c.post(reverse("accounts:address_review", args=[proof.pk]), {"decision": "rejected", "reason": ""})
        proof.refresh_from_db()
        self.assertEqual(proof.status, "pending")

    def test_staff_can_download_the_bill_as_an_attachment(self):
        self.post()
        proof = AddressProof.objects.get(user=self.user)
        c, _ = self.staff_client()
        r = c.get(reverse("accounts:address_document", args=[proof.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertIn("no-store", r["Cache-Control"])
        self.assertTrue(b"".join(r.streaming_content).startswith(b"\xff\xd8\xff"))
