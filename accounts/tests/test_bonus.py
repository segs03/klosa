import shutil
import tempfile
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts.bonus import credit_offer, grant_welcome_bonus
from accounts.kyc_services import review_application, submit_application, verified_types
from accounts.models import Account, CreditPolicy, KYCApplication, VerifiedDocument, WelcomeBonus
from notices.models import Notification
from transactions.models import Transaction

User = get_user_model()
_n = iter(range(1, 10**6))
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
BONUS = dict(WELCOME_BONUS_ENABLED=True, WELCOME_BONUS_AMOUNT="200000", WELCOME_BONUS_BUDGET="1000000",
             CREDIT_MIN_ACCOUNT_AGE_DAYS=30, CREDIT_MIN_BALANCE="10000")


def staff():
    s = User.objects.create_user(f"s{next(_n)}", password="x", is_staff=True)
    s.user_permissions.add(Permission.objects.get(codename="review_kycapplication"))
    return User.objects.get(pk=s.pk)


def customer():
    n = next(_n)
    u = User.objects.create_user(f"c{n}", password="x")
    return u, Account.objects.create(user=u, account_number=str(1000000000 + n))


def apply(user, doc, digest=None):
    return KYCApplication.objects.create(
        user=user, country_code="NG", document_type=doc, legal_name="Ada Obi", date_of_birth=date(1990, 1, 1),
        identity_document="kyc/x.jpg", identity_digest=digest or f"{doc}{user.pk}".ljust(64, "0"))


def approve(app):
    return review_application(app.pk, staff(), "approved", "ok")


@override_settings(**BONUS)
class BonusTests(TestCase):
    def setUp(self):
        cache.clear()
        self.u, self.acc = customer()
        CreditPolicy.objects.create(account_level="verified", currency="NGN",
                                    minimum_limit=Decimal("1000"), maximum_limit=Decimal("500000"))

    def bal(self):
        self.acc.refresh_from_db()
        return self.acc.balance

    def both(self):
        approve(apply(self.u, "nin"))
        approve(apply(self.u, "bvn"))

    def test_one_id_alone_pays_nothing(self):
        approve(apply(self.u, "nin"))
        self.assertEqual((self.bal(), WelcomeBonus.objects.count()), (Decimal("0"), 0))
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.kyc_tier, 0)          # a NIN alone no longer upgrades; only the BVN does

    def test_nin_and_bvn_together_pay_the_gift_once_and_record_it(self):
        self.both()
        self.assertEqual(self.bal(), Decimal("200000"))
        b = WelcomeBonus.objects.get()
        self.assertTrue(Transaction.objects.filter(reference=b.reference, description="Welcome bonus", amount=Decimal("200000")).exists())
        self.assertIsNone(grant_welcome_bonus(self.u))
        self.assertEqual(self.bal(), Decimal("200000"))
        self.assertTrue(verified_types(self.u) >= {"nin", "bvn"})

    def test_the_balance_audit_still_passes(self):
        self.both()
        call_command("audit_balances")

    def test_the_customer_is_told_about_the_gift_and_about_credit(self):
        self.both()
        titles = set(Notification.objects.filter(user=self.u).values_list("title", flat=True))
        self.assertTrue({"Welcome gift", "ID verified", "Credit is coming soon"} <= titles)
        self.assertTrue(Notification.objects.filter(user=self.u, key="credit_offer").exists())

    @override_settings(CREDIT_MIN_ACCOUNT_AGE_DAYS=0)
    def test_an_eligible_customer_gets_the_amount_and_a_link_to_ask(self):
        self.both()
        n = Notification.objects.get(user=self.u, key="credit_offer_ready")
        self.assertIn("500,000", n.body)
        self.assertEqual(n.title, "You qualify for credit")
        self.assertTrue(n.link.endswith("/credit/request/"))

    def test_the_offer_text_counts_down_the_days(self):
        self.both()
        self.acc.refresh_from_db()
        ok, text = credit_offer(self.acc)
        self.assertFalse(ok)
        self.assertIn("30 day", text)

    @override_settings(WELCOME_BONUS_ENABLED=False)
    def test_a_switched_off_gift_pays_nothing(self):
        self.both()
        self.assertEqual(self.bal(), Decimal("0"))

    @override_settings(WELCOME_BONUS_BUDGET="300000")
    def test_the_budget_cap_stops_further_gifts(self):
        self.both()
        u2, acc2 = customer()
        approve(apply(u2, "nin"))
        approve(apply(u2, "bvn"))
        acc2.refresh_from_db()
        self.assertEqual((self.bal(), acc2.balance, WelcomeBonus.objects.count()), (Decimal("200000"), Decimal("0"), 1))

    def test_a_closed_account_gets_nothing(self):
        approve(apply(self.u, "nin"))
        approve(apply(self.u, "bvn"))
        u2, acc2 = customer()
        approve(apply(u2, "nin"))
        bvn = apply(u2, "bvn")
        from django.utils import timezone
        Account.objects.filter(pk=acc2.pk).update(closed_at=timezone.now())
        with self.assertRaises(ValidationError):
            approve(bvn)

    def test_an_id_that_belongs_to_another_person_cannot_be_approved(self):
        approve(apply(self.u, "bvn", digest="b" * 64))
        u2, acc2 = customer()
        dup = apply(u2, "bvn", digest="b" * 64)
        with self.assertRaises(ValidationError):
            approve(dup)
        self.assertFalse(VerifiedDocument.objects.filter(user=u2).exists())

    def test_the_second_id_type_can_be_submitted_but_not_the_first_again(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with override_settings(PRIVATE_MEDIA_ROOT=tmp):
            approve(apply(self.u, "nin"))
            kw = dict(id_number="12345678901", legal_name="Ada Obi", date_of_birth=date(1990, 1, 1))
            self.assertEqual(submit_application(self.u, doc_type="passport", document=SimpleUploadedFile("a.jpg", JPG), **kw).document_type, "passport")
            with self.assertRaises(ValidationError):          # a BVN can no longer be sent as a photo
                submit_application(self.u, doc_type="bvn", document=SimpleUploadedFile("c.jpg", JPG), **kw)
            with self.assertRaises(ValidationError):
                submit_application(self.u, doc_type="nin", document=SimpleUploadedFile("b.jpg", JPG), **kw)

    @override_settings(CREDIT_MIN_ACCOUNT_AGE_DAYS=0, CREDIT_MIN_BALANCE="0")
    def test_the_daily_job_tells_a_qualifying_customer_once(self):
        approve(apply(self.u, "nin"))
        Notification.objects.filter(user=self.u).delete()
        call_command("credit_offers")
        call_command("credit_offers")
        self.assertEqual(Notification.objects.filter(user=self.u, key="credit_offer_ready").count(), 1)