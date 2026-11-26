from datetime import date
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.bvn_provider import BVNNotFound, ProviderError
from accounts.bvn_services import verify_bvn
from accounts.kyc_services import submit_application
from accounts.models import Account, KYCApplication, VerifiedDocument

from .helpers import JPG, make_account, make_user, make_verified

NUMBER = "12345678901"
GOOD = {"first_name": "Ada", "middle_name": "Grace", "last_name": "Obi",
        "date_of_birth": date(1990, 5, 17), "phone": "08012345678"}


class VerifyBvnTests(TestCase):
    def setUp(self):
        self.user = make_user(phone_number="+2348012345678", phone_verified=True)
        self.acc = make_account(self.user)
        patcher = mock.patch("accounts.bvn_services.lookup", return_value=dict(GOOD))
        self.lookup = patcher.start()
        self.addCleanup(patcher.stop)

    def has_bvn(self, user=None):
        return VerifiedDocument.objects.filter(user=user or self.user, document_type="bvn").exists()

    def test_a_matching_phone_verifies_and_returns_the_details(self):
        result = verify_bvn(self.user, NUMBER)
        self.assertEqual(result["name"], "Ada Grace Obi")
        self.assertEqual(result["dob"], "17 May 1990")
        self.assertEqual(result["phone"], "080****5678")
        self.assertTrue(result["bvn"].endswith("8901"))
        self.assertNotIn(NUMBER, result["bvn"])
        self.assertTrue(self.has_bvn())
        self.assertEqual(KYCApplication.objects.get(user=self.user).status, "verified")


    @override_settings(KYC_EMAIL_REQUIRED=False)
    def test_the_account_reaches_tier_two(self):
        verify_bvn(self.user, NUMBER)
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.kyc_tier, 1)

    def test_spaces_and_dashes_in_the_number_are_ignored(self):
        verify_bvn(self.user, "123 4567-8901")
        self.lookup.assert_called_once_with(NUMBER)

    def test_the_raw_number_is_never_stored(self):
        verify_bvn(self.user, NUMBER)
        doc = VerifiedDocument.objects.get(user=self.user)
        app = KYCApplication.objects.get(user=self.user)
        for value in (doc.identity_digest, app.identity_digest, app.legal_name):
            self.assertNotIn(NUMBER, value)

    def test_a_different_phone_is_refused(self):
        self.lookup.return_value = dict(GOOD, phone="08099999999")
        with self.assertRaisesMessage(ValidationError, "do not match"):
            verify_bvn(self.user, NUMBER)
        self.assertFalse(self.has_bvn())

    def test_an_unverified_phone_is_refused_even_if_it_matches(self):
        self.user.phone_verified = False
        self.user.save()
        with self.assertRaisesMessage(ValidationError, "do not match"):
            verify_bvn(self.user, NUMBER)

    def test_a_matching_verified_id_is_used_instead_of_the_phone(self):
        self.user.phone_verified = False
        self.user.save()
        make_verified(self.user, "nin")                      # Ada Obi, 17 May 1990
        verify_bvn(self.user, NUMBER)
        self.assertTrue(self.has_bvn())

    def test_a_different_name_on_a_verified_id_is_refused(self):
        make_verified(self.user, "nin", name="Chidi Okoro")
        with self.assertRaisesMessage(ValidationError, "do not match"):
            verify_bvn(self.user, NUMBER)

    def test_a_different_birth_date_on_a_verified_id_is_refused(self):
        make_verified(self.user, "nin", dob=date(1985, 1, 1))
        with self.assertRaisesMessage(ValidationError, "do not match"):
            verify_bvn(self.user, NUMBER)

    def test_under_18_is_refused(self):
        self.lookup.return_value = dict(GOOD, date_of_birth=date(timezone.localdate().year - 17, 1, 1))
        with self.assertRaisesMessage(ValidationError, "18"):
            verify_bvn(self.user, NUMBER)

    def test_an_unknown_bvn_is_refused(self):
        self.lookup.side_effect = BVNNotFound()
        with self.assertRaisesMessage(ValidationError, "could not find"):
            verify_bvn(self.user, NUMBER)

    def test_a_provider_outage_says_try_again(self):
        self.lookup.side_effect = ProviderError("down")
        with self.assertRaisesMessage(ValidationError, "unavailable"):
            verify_bvn(self.user, NUMBER)
        self.assertFalse(self.has_bvn())

    def test_a_bvn_already_used_on_another_account_is_refused(self):
        verify_bvn(self.user, NUMBER)
        other = make_user()
        make_account(other)
        make_verified(other, "nin")
        with self.assertRaisesMessage(ValidationError, "another account"):
            verify_bvn(other, NUMBER)
        self.assertFalse(self.has_bvn(other))

    def test_verifying_twice_is_refused_without_calling_the_provider(self):
        verify_bvn(self.user, NUMBER)
        self.lookup.reset_mock()
        with self.assertRaisesMessage(ValidationError, "already verified"):
            verify_bvn(self.user, NUMBER)
        self.lookup.assert_not_called()

    def test_a_bad_number_never_reaches_the_provider(self):
        for bad in ("1234", "1234567890a", "", "123456789012"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                verify_bvn(self.user, bad)
        self.lookup.assert_not_called()

    def test_a_closed_account_is_refused(self):
        Account.objects.filter(pk=self.acc.pk).update(closed_at=timezone.now())
        with self.assertRaisesMessage(ValidationError, "not open"):
            verify_bvn(self.user, NUMBER)
        self.lookup.assert_not_called()

    def test_an_open_picture_request_is_reused(self):
        old = KYCApplication.objects.create(
            user=self.user, country_code="NG", document_type="bvn", legal_name="Old Name",
            date_of_birth=date(1990, 5, 17), identity_digest="", status="pending")
        verify_bvn(self.user, NUMBER)
        self.assertEqual(KYCApplication.objects.filter(user=self.user, document_type="bvn").count(), 1)
        old.refresh_from_db()
        self.assertEqual((old.status, old.legal_name), ("verified", "Ada Grace Obi"))

    def test_the_old_picture_path_is_closed(self):
        with self.assertRaisesMessage(ValidationError, "number only"):
            submit_application(self.user, doc_type="bvn", id_number=NUMBER, legal_name="Ada Obi",
                               date_of_birth=date(1990, 5, 17),
                               document=SimpleUploadedFile("a.jpg", JPG))
        self.assertFalse(KYCApplication.objects.filter(user=self.user).exists())
