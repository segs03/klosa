
from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase, override_settings

from accounts.models import (
    Account,
    KYCApplication,
    KYCReview,
    VerifiedIdentity,
)

from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType

from accounts.kyc_services import (
    request_kyc_additional_info,
    reject_kyc_application, approve_kyc_application
)




User = get_user_model()


@override_settings(KYC_IDENTITY_HMAC_KEY="test-secret-key")
class KYCApprovalTests(TestCase):

    def setUp(self):
        self.applicant = User.objects.create_user(
        username="applicant",
        password="test-password",
    )

        self.reviewer_one = User.objects.create_user(
        username="reviewer_one",
        password="test-password",
        is_staff=True,
    )

        self.reviewer_two = User.objects.create_user(
        username="reviewer_two",
        password="test-password",
        is_staff=True,
    )

        self.applicant_account = Account.objects.create(
        user=self.applicant,
        account_number="1234567890",
    )

        self.application = KYCApplication.objects.create(
        user=self.applicant,
        country_code="NG",
        document_type="nin",
        legal_name="Test Applicant",
        date_of_birth=date(2000, 1, 1),
    )

        content_type = ContentType.objects.get_for_model(
        KYCApplication
    )

        permission = Permission.objects.get(
        codename="review_kycapplication",
        content_type=content_type,
    )

        self.reviewer_one.user_permissions.add(permission)
        self.reviewer_two.user_permissions.add(permission)

        self.digest = "a" * 64

    def approve(self, reviewer, digest=None):
        return approve_kyc_application(
            application_id=self.application.pk,
            reviewer=reviewer,
            identity_digest=digest or self.digest,
        )

    def test_first_approval_waits_for_second_reviewer(self):
        result = self.approve(self.reviewer_one)

        self.application.refresh_from_db()

        self.assertEqual(
            result["status"],
            "awaiting_second_approval",
        )
        self.assertEqual(self.application.status, "under_review")
        self.assertEqual(
            KYCReview.objects.filter(
                application=self.application,
                decision="approved",
            ).count(),
            1,
        )
        self.assertFalse(
            VerifiedIdentity.objects.filter(
                user=self.applicant,
            ).exists()
        )

    def test_second_reviewer_completes_verification(self):
        self.approve(self.reviewer_one)

        result = self.approve(self.reviewer_two)

        self.application.refresh_from_db()

        self.assertEqual(result["status"], "verified")
        self.assertEqual(self.application.status, "verified")
        self.assertTrue(
            VerifiedIdentity.objects.filter(
                user=self.applicant,
                identity_digest=self.digest,
            ).exists()
        )

    def test_second_reviewer_must_use_same_digest(self):
        self.approve(self.reviewer_one)

        with self.assertRaises(ValidationError):
            self.approve(
                self.reviewer_two,
                digest="b" * 64,
            )

        self.assertEqual(
            KYCReview.objects.filter(
                application=self.application,
                decision="approved",
            ).count(),
            1,
        )

    def test_same_reviewer_cannot_approve_twice(self):
        self.approve(self.reviewer_one)

        with self.assertRaises(ValidationError):
            self.approve(self.reviewer_one)

    def test_applicant_cannot_review_own_application(self):
        self.applicant.is_staff = True
        self.applicant.user_permissions.add(
            self.reviewer_one.user_permissions.get(
                codename="review_kycapplication"
            )
        )

        with self.assertRaises(PermissionDenied):
            self.approve(self.applicant)

    def test_staff_without_permission_cannot_review(self):
        unauthorised = User.objects.create_user(
            username="unauthorised",
            password="test-password",
            is_staff=True,
        )

        with self.assertRaises(PermissionDenied):
            self.approve(unauthorised)

    def test_duplicate_verified_identity_is_rejected(self):
        other_user = User.objects.create_user(
            username="other_user",
            password="test-password",
        )

        VerifiedIdentity.objects.create(
            user=other_user,
            country_code="NG",
            document_type="nin",
            identity_digest=self.digest,
            verified_at=self.application.submitted_at,
            source_application=KYCApplication.objects.create(
                user=other_user,
                country_code="NG",
                document_type="nin",
                legal_name="Other User",
                date_of_birth=date(2000, 1, 1),
            ),
        )

        with self.assertRaises(ValidationError):
            self.approve(self.reviewer_one)

    def test_rejected_application_cannot_be_approved(self):
        self.application.status = "rejected"
        self.application.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            self.approve(self.reviewer_one)

    def test_invalid_digest_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.approve(
                self.reviewer_one,
                digest="invalid",
            )
            


    def test_additional_info_request_updates_application(self):
        result = request_kyc_additional_info(
        application_id=self.application.pk,
        reviewer=self.reviewer_one,
        reason="Please provide a clearer identity document.",
    )

        self.application.refresh_from_db()

        self.assertEqual(result["status"], "additional_info")
        self.assertEqual(self.application.status, "additional_info")


    def test_first_rejection_waits_for_second_reviewer(self):
        result = reject_kyc_application(
        application_id=self.application.pk,
        reviewer=self.reviewer_one,
        reason="Identity details require further review.",
    )

        self.application.refresh_from_db()

        self.assertEqual(
        result["status"],
        "awaiting_second_rejection",
    )
        self.assertEqual(self.application.status, "under_review")


    def test_second_rejection_rejects_application(self):
        reject_kyc_application(
        application_id=self.application.pk,
        reviewer=self.reviewer_one,
        reason="Identity verification failed.",
    )

        result = reject_kyc_application(
        application_id=self.application.pk,
        reviewer=self.reviewer_two,
        reason="Independent review confirms the rejection.",
    )

        self.application.refresh_from_db()

        self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.application.status, "rejected")


    def test_reviewer_cannot_submit_empty_reason(self):
        with self.assertRaises(ValidationError):
            reject_kyc_application(
            application_id=self.application.pk,
            reviewer=self.reviewer_one,
            reason=" ",
        )


    def test_reviewer_cannot_reject_after_approval_started(self):
        from accounts.kyc_services import approve_kyc_application

        approve_kyc_application(
        application_id=self.application.pk,
        reviewer=self.reviewer_one,
        identity_digest=self.digest,
    )

        with self.assertRaises(ValidationError):
            reject_kyc_application(
            application_id=self.application.pk,
            reviewer=self.reviewer_two,
            reason="Attempted rejection.",
        )
