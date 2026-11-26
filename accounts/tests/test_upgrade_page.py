from datetime import date

from django.contrib.messages import get_messages
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Account, AddressProof

from .helpers import PASSWORD, make_account, make_user, make_verified

from django.test import override_settings



@override_settings(KYC_ADDRESS_REQUIRED=True, KYC_EMAIL_REQUIRED=False)
class UpgradePageTests(TestCase):
    def setUp(self):
        self.user = make_user("ann")
        self.acc = make_account(self.user)
        self.client.login(username="ann", password=PASSWORD)
        self.url = reverse("accounts:upgrade")

    def page(self):
        return self.client.get(self.url)

    def nxt(self):
        return self.page().context["nxt"]

    def address(self):
        AddressProof.objects.create(user=self.user, line1="12 Admiralty Way", city="Lagos", state="Lagos",
                                    bill_date=date.today(), document="address/x.jpg", status="approved")

    def face(self):
        Account.objects.filter(pk=self.acc.pk).update(face_verified_at=timezone.now())

    def test_you_must_be_logged_in(self):
        self.assertEqual(Client().get(self.url).status_code, 302)

    def test_the_bvn_always_comes_first_and_a_nin_does_not_replace_it(self):
        label, link = self.nxt()
        self.assertIn("BVN", label)
        self.assertEqual(link, reverse("accounts:verify"))
        make_verified(self.user, "nin")
        self.assertIn("BVN", self.nxt()[0])

    def test_after_the_bvn_it_asks_for_one_more_id(self):
        make_verified(self.user, "bvn")
        label, link = self.nxt()
        self.assertIn("one more ID", label)
        self.assertEqual(link, reverse("accounts:verify"))

    def test_after_two_ids_it_asks_for_the_address(self):
        make_verified(self.user, "bvn")
        make_verified(self.user, "passport")
        self.assertEqual(self.nxt()[1], reverse("accounts:address"))

    def test_after_the_address_it_asks_for_the_face_check(self):
        make_verified(self.user, "bvn")
        make_verified(self.user, "drivers_license")
        self.address()
        self.assertEqual(self.nxt()[1], reverse("face_auth:begin", args=["upgrade"]))

    def test_when_everything_is_done_there_is_nothing_left(self):
        make_verified(self.user, "bvn")
        make_verified(self.user, "nin")
        self.address()
        self.face()
        self.assertIsNone(self.nxt())

    @override_settings(KYC_ADDRESS_REQUIRED=False)
    def test_the_address_step_is_skipped_when_the_rule_is_off(self):
        make_verified(self.user, "bvn")
        make_verified(self.user, "nin")
        self.assertEqual(self.nxt()[1], reverse("face_auth:begin", args=["upgrade"]))

    def test_the_checklist_shows_each_requirement(self):
        make_verified(self.user, "bvn")
        make_verified(self.user, "nin")
        checks = dict(self.page().context["checks"])
        self.assertEqual(checks, {"Phone": True, "BVN": True, "Email": True, "One more ID": True,
                                  "Address": False, "Face check": False})

    def test_the_tier_table_matches_the_new_requirements(self):
        rows = self.page().context["rows"]
        self.assertEqual([r["n"] for r in rows], [1, 2, 3])
        self.assertIn("BVN", rows[1]["need"])
        self.assertIn("address", rows[2]["need"])


class RecheckTests(TestCase):
    def setUp(self):
        self.user = make_user("ann")
        self.acc = make_account(self.user)
        self.url = reverse("accounts:recheck")

    def test_it_needs_a_login_and_a_post(self):
        self.assertEqual(Client().post(self.url).status_code, 302)
        self.client.login(username="ann", password=PASSWORD)
        self.assertEqual(self.client.get(self.url).status_code, 405)


    @override_settings(KYC_EMAIL_REQUIRED=False)
    def test_it_raises_the_tier_and_says_so(self):
        make_verified(self.user, "bvn")
        self.client.login(username="ann", password=PASSWORD)
        r = self.client.post(self.url)
        self.assertRedirects(r, reverse("accounts:upgrade"), fetch_redirect_response=False)
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.kyc_tier, 1)
        self.assertIn("Tier 2", [str(m) for m in get_messages(r.wsgi_request)][0])

    def test_a_nin_alone_does_not_raise_it(self):
        make_verified(self.user, "nin")
        self.client.login(username="ann", password=PASSWORD)
        self.client.post(self.url)
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.kyc_tier, 0)
