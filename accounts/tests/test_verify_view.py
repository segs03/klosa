from datetime import date
from unittest import mock

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import KYCApplication

from .helpers import JPG, PASSWORD, make_account, make_user, make_verified

DETAILS = {"bvn": "•••••••8901", "name": "Ada Grace Obi", "dob": "17 May 1990", "phone": "080****5678"}


class VerifyViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("ann")
        self.acc = make_account(self.user)
        self.client.login(username="ann", password=PASSWORD)
        self.url = reverse("accounts:verify")

    def options(self, response=None):
        r = response or self.client.get(self.url)
        return [value for value, _ in r.context["type_options"]]

    def post_bvn(self, **over):
        data = {"doc_type": "bvn", "id_number": "12345678901", "consent": "on"}
        data.update(over)
        return self.client.post(self.url, data)

    def test_you_must_be_logged_in(self):
        self.assertEqual(Client().get(self.url).status_code, 302)

    def test_a_new_customer_starts_with_the_bvn(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.options(r)[0], "bvn")
        self.assertEqual([s["code"] for s in r.context["steps"]], ["bvn", "id", "address"])
        self.assertContains(r, "Proof of address")
        self.assertContains(r, reverse("accounts:address"))

    def test_a_good_bvn_shows_the_details_once(self):
        with mock.patch("accounts.kyc_views.verify_bvn", return_value=DETAILS) as v:
            r = self.post_bvn()
        self.assertRedirects(r, self.url, fetch_redirect_response=False)
        v.assert_called_once_with(self.user, "12345678901")
        first = self.client.get(self.url)
        self.assertContains(first, "Ada Grace Obi")
        self.assertContains(first, "✔ Valid")
        self.assertNotContains(self.client.get(self.url), "✔ Valid")

    def test_a_refused_bvn_shows_the_reason_under_the_number(self):
        with mock.patch("accounts.kyc_views.verify_bvn",
                        side_effect=ValidationError("These BVN details do not match your account.")):
            r = self.post_bvn()
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "do not match your account")

    def test_the_bvn_needs_consent_and_a_valid_number(self):
        with mock.patch("accounts.kyc_views.verify_bvn") as v:
            self.assertEqual(self.post_bvn(consent="").status_code, 200)
            r = self.post_bvn(id_number="1234")
            self.assertContains(r, "exactly 11 digits")
        v.assert_not_called()

    def test_after_the_bvn_only_the_second_id_types_remain(self):
        make_verified(self.user, "bvn")
        self.assertEqual(self.options(), ["nin", "passport", "drivers_license"])

    def test_a_second_id_can_be_submitted_with_a_photo(self):
        make_verified(self.user, "bvn")
        r = self.client.post(self.url, {
            "doc_type": "passport", "id_number": "A1234567", "legal_name": "Ada Obi",
            "date_of_birth": "1990-05-17", "consent": "on",
            "document": SimpleUploadedFile("id.jpg", JPG, "image/jpeg")})
        self.assertRedirects(r, self.url, fetch_redirect_response=False)
        self.assertEqual(KYCApplication.objects.get(user=self.user, document_type="passport").status, "pending")

    def test_a_second_id_in_review_closes_the_other_second_id_options(self):
        KYCApplication.objects.create(
            user=self.user, country_code="NG", document_type="passport", legal_name="Ada Obi",
            date_of_birth=date(1990, 5, 17), identity_digest="a" * 64, status="pending")
        self.assertEqual(self.options(), ["bvn"])

    def test_a_verified_customer_has_nothing_left_to_submit(self):
        make_verified(self.user, "bvn")
        make_verified(self.user, "drivers_license")
        r = self.client.get(self.url)
        self.assertFalse(r.context["can_submit"])
        self.assertTrue(r.context["both"])

    def test_a_bvn_cannot_be_posted_when_it_is_already_verified(self):
        make_verified(self.user, "bvn")
        with mock.patch("accounts.kyc_views.verify_bvn") as v:
            self.post_bvn()
        v.assert_not_called()

    @override_settings(WELCOME_BONUS_ENABLED=True, WELCOME_BONUS_AMOUNT="1000")
    def test_the_welcome_gift_is_advertised(self):
        self.assertContains(self.client.get(self.url), "welcome gift")
