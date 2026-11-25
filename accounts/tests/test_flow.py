from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Account, CreditAccount, CreditApplication, KYCApplication, VerifiedDocument
from beneficiary.models import Beneficiary

User = get_user_model()
_n = iter(range(1, 10**6))


def customer(balance="0"):
    n = next(_n)
    u = User.objects.create_user(f"u{n}", password="pass12345", first_name="Ann", last_name="Lee")
    return u, Account.objects.create(user=u, account_number=str(1000000000 + n), balance=Decimal(balance))


def login(u):
    c = Client()
    c.login(username=u.username, password="pass12345")
    return c


def doc(user, kind, status="verified"):
    a = KYCApplication.objects.create(user=user, country_code="NG", document_type=kind, legal_name="Ada Obi",
                                      date_of_birth=date(1990, 1, 1), identity_document="kyc/x.jpg",
                                      identity_digest=f"{kind}{user.pk}".ljust(64, "0"), status=status)
    if status == "verified":
        VerifiedDocument.objects.create(user=user, document_type=kind, identity_digest=a.identity_digest,
                                        verified_at=timezone.now(), source_application=a)
    return a


class VerifyPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.u, self.acc = customer()
        self.c = login(self.u)
        self.url = reverse("accounts:verify")

    def test_after_the_nin_is_in_review_the_bvn_can_still_be_submitted(self):
        doc(self.u, "nin", "pending")
        r = self.c.get(self.url)
        self.assertTrue(r.context["can_submit"])
        self.assertEqual(r.context["next_type"], "bvn")
        self.assertContains(r, "In review")

    def test_a_verified_nin_moves_the_form_to_the_bvn(self):
        doc(self.u, "nin")
        r = self.c.get(self.url)
        self.assertEqual(r.context["next_type"], "bvn")
        self.assertEqual([s["state"] for s in r.context["steps"]], ["todo", "done", "todo"])

    def test_when_more_information_is_needed_the_reason_and_the_form_show(self):
        a = doc(self.u, "nin", "additional_info")
        s = User.objects.create_user("rev", password="x", is_staff=True)
        from accounts.models import KYCReview
        KYCReview.objects.create(application=a, reviewer=s, decision="additional_info", reason="Photo is blurry")
        r = self.c.get(self.url)
        self.assertTrue(r.context["can_submit"])
        self.assertContains(r, "Photo is blurry")

    def test_both_verified_shows_the_finished_state(self):
        doc(self.u, "nin")
        doc(self.u, "bvn")
        r = self.c.get(self.url)
        self.assertTrue(r.context["both"])
        self.assertFalse(r.context["can_submit"])
        self.assertContains(r, "fully verified")


class StaffReviewPageTests(TestCase):
    def setUp(self):
        self.u, _ = customer()
        self.app = doc(self.u, "nin", "pending")
        self.staff = User.objects.create_user("rev", password="pass12345", is_staff=True)
        self.staff.user_permissions.add(Permission.objects.get(codename="review_kycapplication"))
        self.url = reverse("accounts:kyc_review", args=[self.app.pk])

    def test_a_reviewer_sees_the_decision_form(self):
        r = login(User.objects.get(pk=self.staff.pk)).get(self.url)
        self.assertTrue(r.context["can_decide"])
        self.assertContains(r, "Save decision")

    def test_you_are_told_why_you_cannot_decide(self):
        s = User.objects.get(pk=self.staff.pk)
        KYCApplication.objects.filter(pk=self.app.pk).update(user=s)
        Account.objects.create(user=s, account_number="1999999999")
        r = login(s).get(self.url)
        self.assertFalse(r.context["can_decide"])
        self.assertContains(r, "your own ID request")

    def test_a_decided_request_explains_itself(self):
        KYCApplication.objects.filter(pk=self.app.pk).update(status="verified")
        r = login(User.objects.get(pk=self.staff.pk)).get(self.url)
        self.assertContains(r, "verified")


@override_settings(CREDIT_MIN_ACCOUNT_AGE_DAYS=0, CREDIT_MIN_BALANCE="0")
class CreditPageTests(TestCase):
    def setUp(self):
        self.u, self.acc = customer("20000")
        self.c = login(self.u)
        self.url = reverse("accounts:credit")

    def test_an_approved_facility_shows_the_limit_and_what_is_available(self):
        CreditAccount.objects.create(account=self.acc, credit_limit=Decimal("80000"),
                                     outstanding_balance=Decimal("5000"), status="active")
        r = self.c.get(self.url)
        self.assertContains(r, "75,000.00")
        self.assertContains(r, "80,000")
        self.assertContains(r, "Order your credit card")

    def test_a_pending_application_is_explained(self):
        CreditApplication.objects.create(user=self.u, requested_amount=Decimal("50000"))
        self.assertContains(self.c.get(self.url), "being reviewed" if False else "reviewing it")

    def test_a_declined_application_shows_the_reason_and_lets_you_ask_again(self):
        CreditApplication.objects.create(user=self.u, requested_amount=Decimal("50000"),
                                         status="rejected", decision_reason="Income too low")
        r = self.c.get(self.url)
        self.assertContains(r, "Income too low")
        self.assertContains(r, "Request credit")

    def test_a_new_customer_sees_the_request_button(self):
        self.assertContains(self.c.get(self.url), "Request credit")

    def test_a_suspended_line_still_offers_repayment(self):
        CreditAccount.objects.create(account=self.acc, credit_limit=Decimal("1000"),
                                     outstanding_balance=Decimal("500"), status="suspended")
        r = self.c.get(self.url)
        self.assertContains(r, "suspended")
        self.assertContains(r, "Repay")


class PrefillTests(TestCase):
    def setUp(self):
        self.u, self.acc = customer("50000")
        self.u2, self.acc2 = customer()
        self.c = login(self.u)
        self.ben = Beneficiary.objects.create(owner=self.u, name="Ben", account_number=self.acc2.account_number,
                                              bank_code="999", bank_name="Klosa Bank")

    @override_settings(KLOSA_BANK_CODE="999")
    def test_a_recipient_pre_selects_the_send_page_and_the_request_page(self):
        r = self.c.get(reverse("transfers:send") + f"?to={self.ben.pk}")
        self.assertEqual(r.context["form"].initial.get("beneficiary"), self.ben.pk)
        r2 = self.c.get(reverse("transfers:request_money") + f"?to={self.ben.pk}")
        self.assertEqual(r2.context["form"].initial.get("account_number"), self.acc2.account_number)

    def test_someone_elses_recipient_is_ignored(self):
        other = Beneficiary.objects.create(owner=self.u2, name="X", account_number="1000000009",
                                           bank_code="999", bank_name="Klosa Bank")
        r = self.c.get(reverse("transfers:send") + f"?to={other.pk}")
        self.assertNotIn("beneficiary", r.context["form"].initial)

    def test_junk_in_the_link_is_ignored(self):
        for q in ("abc", "999999", "1;DROP", ""):
            self.assertEqual(self.c.get(reverse("transfers:send") + f"?to={q}").status_code, 200)