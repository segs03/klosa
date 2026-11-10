from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Account, CreditAccount

User = get_user_model()

class AccountModelTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            password="testpassword123"
        )

        self.account = Account.objects.create(
            user=self.user,
            account_number="1234567890"
        )

    def test_account_creation(self):
        self.assertEqual(self.account.user, self.user)
        self.assertEqual(self.account.balance, Decimal("0"))
        self.assertEqual(self.account.currency, "NGN")

    def test_account_string_representation(self):
        self.assertEqual(
            str(self.account),
            "testuser - 1234567890"
        )

    def test_set_pin(self):
        self.account.set_pin("1234")
        self.account.refresh_from_db()

        self.assertTrue(self.account.pin_hash)
        self.assertNotEqual(self.account.pin_hash, "1234")
        self.assertEqual(self.account.pin_attempts, 0)
        self.assertIsNone(self.account.locked_until)

    def test_correct_pin(self):
        self.account.set_pin("1234")

        self.assertTrue(self.account.check_pin("1234"))

    def test_incorrect_pin_increments_attempts(self):
        self.account.set_pin("1234")

        self.assertFalse(self.account.check_pin("9999"))

        self.account.refresh_from_db()
        self.assertEqual(self.account.pin_attempts, 1)

    def test_third_failed_attempt_locks_account(self):
        self.account.set_pin("1234")

        for _ in range(3):
            self.account.check_pin("9999")

        self.account.refresh_from_db()

        self.assertEqual(self.account.pin_attempts, 0)
        self.assertIsNotNone(self.account.locked_until)
        self.assertTrue(self.account.is_locked())

    def test_locked_account_rejects_correct_pin(self):
        self.account.set_pin("1234")
        self.account.locked_until = timezone.now() + timedelta(minutes=15)
        self.account.save(update_fields=["locked_until"])

        self.assertFalse(self.account.check_pin("1234"))

    def test_expired_lock_does_not_count_as_locked(self):
        self.account.locked_until = timezone.now() - timedelta(minutes=1)
        self.account.save(update_fields=["locked_until"])

        self.assertFalse(self.account.is_locked())

    def test_correct_pin_resets_failed_attempts(self):
        self.account.set_pin("1234")
        self.account.pin_attempts = 2
        self.account.save(update_fields=["pin_attempts"])

        self.assertTrue(self.account.check_pin("1234"))

        self.account.refresh_from_db()
        self.assertEqual(self.account.pin_attempts, 0)

    def test_empty_pin_hash_rejects_pin(self):
        self.assertFalse(self.account.check_pin("1234"))


class CreditAccountModelTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="credituser",
            password="testpassword123"
        )

        self.account = Account.objects.create(
            user=self.user,
            account_number="9876543210"
        )

        self.credit = CreditAccount.objects.create(
            account=self.account,
            credit_limit=Decimal("100000.00"),
            outstanding_balance=Decimal("25000.00")
        )

    def test_credit_account_creation(self):
        self.assertEqual(self.credit.status, "pending")
        self.assertEqual(self.credit.credit_score, 650)

    def test_available_credit(self):
        self.assertEqual(
            self.credit.available_credit,
            Decimal("75000.00")
        )

    def test_available_credit_cannot_be_negative(self):
        self.credit.outstanding_balance = Decimal("120000.00")

        self.assertEqual(
            self.credit.available_credit,
            Decimal("0.00")
        )

    def test_credit_account_string_representation(self):
        self.assertEqual(
            str(self.credit),
            "credituser Credit Facility"
        )