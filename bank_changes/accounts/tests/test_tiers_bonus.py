from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.bonus import grant_welcome_bonus
from accounts.models import Account, AddressProof, WelcomeBonus
from accounts.tiers import earned, has_required_ids, refresh_tier
from transactions.models import Transaction

from .helpers import make_account, make_user, make_verified


def approved_address(user):
    return AddressProof.objects.create(
        user=user, line1="12 Admiralty Way", city="Lagos", state="Lagos", bill_date=date.today(),
        document="address/x.jpg", status="approved")


class HasRequiredIdsTests(TestCase):
    def test_the_rule(self):
        self.assertFalse(has_required_ids(set()))
        self.assertFalse(has_required_ids({"bvn"}))
        self.assertFalse(has_required_ids({"nin", "passport"}))
        for second in ("nin", "passport", "drivers_license"):
            self.assertTrue(has_required_ids({"bvn", second}))


@override_settings(KYC_ADDRESS_REQUIRED=True)
class TierTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.acc = make_account(self.user)

    def tier(self):
        return earned(self.user, Account.objects.get(pk=self.acc.pk))

    def face(self):
        Account.objects.filter(pk=self.acc.pk).update(face_verified_at=timezone.now())

    def test_nothing_verified_earns_nothing(self):
        self.assertEqual(self.tier(), 0)

    def test_a_nin_alone_no_longer_counts(self):
        make_verified(self.user, "nin")
        self.assertEqual(self.tier(), 0)

    def test_a_bvn_alone_reaches_tier_two(self):
        make_verified(self.user, "bvn")
        self.assertEqual(self.tier(), 1)

    def test_the_top_tier_needs_every_requirement(self):
        make_verified(self.user, "bvn")
        make_verified(self.user, "nin")
        self.assertEqual(self.tier(), 1)                 # no face check, no address
        self.face()
        self.assertEqual(self.tier(), 1)                 # still no address
        approved_address(self.user)
        self.assertEqual(self.tier(), 2)

    def test_any_second_id_works(self):
        for second in ("passport", "drivers_license"):
            with self.subTest(second=second):
                user = make_user()
                acc = make_account(user, face_verified_at=timezone.now())
                make_verified(user, "bvn")
                make_verified(user, second)
                approved_address(user)
                self.assertEqual(earned(user, Account.objects.get(pk=acc.pk)), 2)

    def test_a_pending_or_rejected_address_does_not_count(self):
        make_verified(self.user, "bvn")
        make_verified(self.user, "nin")
        self.face()
        for status in ("pending", "rejected"):
            AddressProof.objects.filter(user=self.user).delete()
            AddressProof.objects.create(user=self.user, line1="12 Admiralty Way", city="Lagos",
                                        state="Lagos", bill_date=date.today(), document="a.jpg", status=status)
            self.assertEqual(self.tier(), 1)

    @override_settings(KYC_ADDRESS_REQUIRED=False)
    def test_the_address_rule_can_be_switched_off(self):
        make_verified(self.user, "bvn")
        make_verified(self.user, "nin")
        self.face()
        self.assertEqual(self.tier(), 2)

    def test_refresh_raises_the_tier(self):
        make_verified(self.user, "bvn")
        refresh_tier(self.user)
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.kyc_tier, 1)

    def test_refresh_never_lowers_a_tier(self):
        Account.objects.filter(pk=self.acc.pk).update(kyc_tier=2)
        refresh_tier(self.user)
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.kyc_tier, 2)

    def test_refresh_ignores_a_closed_account(self):
        make_verified(self.user, "bvn")
        Account.objects.filter(pk=self.acc.pk).update(closed_at=timezone.now())
        self.assertIsNone(refresh_tier(self.user))


@override_settings(WELCOME_BONUS_ENABLED=True, WELCOME_BONUS_AMOUNT="1000", WELCOME_BONUS_BUDGET="5000")
class BonusTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.acc = make_account(self.user)
        patcher = mock.patch("accounts.bonus.send_credit_offer")
        patcher.start()
        self.addCleanup(patcher.stop)

    def balance(self, acc=None):
        return Account.objects.get(pk=(acc or self.acc).pk).balance

    def test_bvn_plus_any_second_id_pays(self):
        for second in ("nin", "passport", "drivers_license"):
            with self.subTest(second=second):
                user = make_user()
                acc = make_account(user)
                make_verified(user, "bvn")
                make_verified(user, second)
                self.assertIsNotNone(grant_welcome_bonus(user))
                self.assertEqual(self.balance(acc), Decimal("1000"))

    def test_it_is_recorded_as_a_transaction(self):
        make_verified(self.user, "bvn")
        make_verified(self.user, "nin")
        bonus = grant_welcome_bonus(self.user)
        tx = Transaction.objects.get(account=self.acc, reference=bonus.reference)
        self.assertEqual((tx.transaction_type, tx.amount, tx.description), ("deposit", Decimal("1000"), "Welcome bonus"))

    def test_bvn_alone_does_not_pay(self):
        make_verified(self.user, "bvn")
        self.assertIsNone(grant_welcome_bonus(self.user))
        self.assertEqual(self.balance(), Decimal("0"))

    def test_other_ids_without_a_bvn_do_not_pay(self):
        make_verified(self.user, "nin")
        make_verified(self.user, "passport")
        self.assertIsNone(grant_welcome_bonus(self.user))

    def test_it_pays_only_once(self):
        make_verified(self.user, "bvn")
        make_verified(self.user, "nin")
        grant_welcome_bonus(self.user)
        self.assertIsNone(grant_welcome_bonus(self.user))
        self.assertEqual(self.balance(), Decimal("1000"))
        self.assertEqual(WelcomeBonus.objects.filter(user=self.user).count(), 1)

    @override_settings(WELCOME_BONUS_BUDGET="1500")
    def test_it_stops_when_the_budget_is_used_up(self):
        make_verified(self.user, "bvn")
        make_verified(self.user, "nin")
        self.assertIsNotNone(grant_welcome_bonus(self.user))
        second = make_user()
        acc = make_account(second)
        make_verified(second, "bvn")
        make_verified(second, "nin")
        self.assertIsNone(grant_welcome_bonus(second))
        self.assertEqual(self.balance(acc), Decimal("0"))

    @override_settings(WELCOME_BONUS_ENABLED=False)
    def test_it_can_be_switched_off(self):
        make_verified(self.user, "bvn")
        make_verified(self.user, "nin")
        self.assertIsNone(grant_welcome_bonus(self.user))

    def test_a_closed_account_is_not_paid(self):
        make_verified(self.user, "bvn")
        make_verified(self.user, "nin")
        Account.objects.filter(pk=self.acc.pk).update(closed_at=timezone.now())
        self.assertIsNone(grant_welcome_bonus(self.user))
