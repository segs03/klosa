from datetime import date

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from accounts.models import Account, AddressProof, KYCApplication, VerifiedDocument
from accounts.tiers import refresh_tier
from django.test import Client, TestCase, override_settings



User = get_user_model()


def doc(user, kind):
    app = KYCApplication.objects.create(
        user=user, country_code="NG", document_type=kind, legal_name="Ada Obi", date_of_birth=date(1990, 1, 1),
        identity_document="kyc/x.jpg", identity_digest=f"{kind}{user.pk}".ljust(64, "0"), status="verified")
    VerifiedDocument.objects.create(user=user, document_type=kind, identity_digest=app.identity_digest,
                                    verified_at=timezone.now(), source_application=app)


def approved_address(user):
    return AddressProof.objects.create(user=user, line1="12 Admiralty Way", city="Lagos", state="Lagos",
                                       bill_date=date.today(), document="address/x.jpg", status="approved")



@override_settings(KYC_ADDRESS_REQUIRED=True)
class TierTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("ann", password="pass12345")
        self.acc = Account.objects.create(user=self.u, account_number="1000000001")

    def tier(self):
        self.acc.refresh_from_db()
        return self.acc.tier

    def test_a_new_account_is_tier_one(self):
        self.assertEqual(self.acc.tier, 1)

    def test_one_id_makes_tier_two(self):
        doc(self.u, "bvn")
        refresh_tier(self.u)
        self.assertEqual(self.tier(), 2)

    def test_both_ids_without_a_face_check_stay_tier_two(self):
        doc(self.u, "nin")
        doc(self.u, "bvn")
        refresh_tier(self.u)
        self.assertEqual(self.tier(), 2)

    def test_both_ids_and_a_face_make_tier_three(self):
        approved_address(self.u)
        doc(self.u, "nin")
        doc(self.u, "bvn")
        Account.objects.filter(pk=self.acc.pk).update(face_verified_at=timezone.now())
        refresh_tier(self.u)
        self.assertEqual(self.tier(), 3)

    def test_without_an_approved_address_the_top_tier_is_not_reached(self):
        doc(self.u, "nin")
        doc(self.u, "bvn")
        Account.objects.filter(pk=self.acc.pk).update(face_verified_at=timezone.now())
        refresh_tier(self.u)
        self.assertEqual(self.tier(), 2)

    def test_a_nin_alone_does_not_upgrade(self):
        doc(self.u, "nin")
        refresh_tier(self.u)
        self.assertEqual(self.tier(), 1)

    def test_a_passport_works_as_the_second_id(self):
        doc(self.u, "bvn")
        doc(self.u, "passport")
        approved_address(self.u)
        Account.objects.filter(pk=self.acc.pk).update(face_verified_at=timezone.now())
        refresh_tier(self.u)
        self.assertEqual(self.tier(), 3)

    def test_a_face_alone_changes_nothing(self):
        Account.objects.filter(pk=self.acc.pk).update(face_verified_at=timezone.now())
        refresh_tier(self.u)
        self.assertEqual(self.tier(), 1)

    def test_a_tier_is_never_lowered_and_a_closed_account_is_untouched(self):
        Account.objects.filter(pk=self.acc.pk).update(kyc_tier=2)
        refresh_tier(self.u)
        self.assertEqual(self.tier(), 3)
        Account.objects.filter(pk=self.acc.pk).update(kyc_tier=0, closed_at=timezone.now())
        doc(self.u, "nin")
        refresh_tier(self.u)
        self.assertEqual(self.tier(), 1)

    def test_the_upgrade_page_names_the_next_step(self):
        c = Client()
        c.login(username="ann", password="pass12345")
        url = reverse("accounts:upgrade")
        self.assertEqual(c.get(url).context["nxt"][1], reverse("accounts:verify"))
        doc(self.u, "nin")
        self.assertIn("BVN", c.get(url).context["nxt"][0])
        doc(self.u, "bvn")
        self.assertEqual(c.get(url).context["nxt"][1], reverse("accounts:address"))
        approved_address(self.u)
        self.assertEqual(c.get(url).context["nxt"][1], reverse("face_auth:begin", args=["upgrade"]))
        Account.objects.filter(pk=self.acc.pk).update(face_verified_at=timezone.now())
        self.assertIsNone(c.get(url).context["nxt"])

    def test_recheck_needs_post_and_a_login(self):
        c = Client()
        self.assertEqual(c.post(reverse("accounts:recheck")).status_code, 302)
        c.login(username="ann", password="pass12345")
        self.assertEqual(c.get(reverse("accounts:recheck")).status_code, 405)