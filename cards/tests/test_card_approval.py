from django.test import TestCase
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied

from accounts.models import Account
from cards.models import CardOrder, BankCard
from cards.services import (
    approve_physical_card_order,
    reject_physical_card_order,
    CardIssuanceError,
)

User = get_user_model()


class PhysicalCardApprovalTests(TestCase):

    def setUp(self):
        self.customer = User.objects.create_user(
            username="customer_test",
            password="test-password",
        )

        self.staff = User.objects.create_user(
            username="card_staff",
            password="test-password",
            is_staff=True,
        )

        self.account = Account.objects.create(
            user=self.customer,
            account_number="1234567890",
        )

        self.order = CardOrder.objects.create(
            account=self.account,
            card_type="debit",
            card_format="physical",
            cardholder_name="Test Customer",
            status="pending",
        )

        permission = Permission.objects.get(
            codename="approve_cardorder",
            content_type__app_label="cards",
        )
        self.staff.user_permissions.add(permission)

    def test_unauthorised_staff_cannot_approve(self):
        with self.assertRaises(CardIssuanceError):
            approve_physical_card_order(
                self.order.pk,
                self.customer,
            )

    def test_authorised_staff_can_approve(self):
        card = approve_physical_card_order(
            self.order.pk,
            self.staff,
        )

        self.order.refresh_from_db()

        self.assertIsInstance(card, BankCard)
        self.assertEqual(self.order.status, "issued")
        self.assertEqual(self.order.bank_card_id, card.pk)

    def test_approved_order_is_not_issued_twice(self):
        first_card = approve_physical_card_order(
            self.order.pk,
            self.staff,
        )

        second_card = approve_physical_card_order(
            self.order.pk,
            self.staff,
        )

        self.assertEqual(first_card.pk, second_card.pk)
        self.assertEqual(
            BankCard.objects.filter(account=self.account).count(),
            1,
        )

    def test_rejection_requires_a_reason(self):
        with self.assertRaises(CardIssuanceError):
            reject_physical_card_order(
                self.order.pk,
                self.staff,
                "",
            )

    def test_authorised_staff_can_reject(self):
        reject_physical_card_order(
            self.order.pk,
            self.staff,
            "Customer information requires review.",
        )

        self.order.refresh_from_db()

        self.assertEqual(self.order.status, "rejected")
        self.assertEqual(
            self.order.rejection_reason,
            "Customer information requires review.",
        )