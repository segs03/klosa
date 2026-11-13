
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.utils import timezone

from accounts.models import Account
from cards.models import CardOrder, BankCard, CardDelivery
from cards.services import (
    issue_virtual_card_order,
    approve_physical_card_order,
    update_card_delivery,
    CardIssuanceError,
)

User = get_user_model()


class CardLifecycleTests(TestCase):

    def setUp(self):
        self.customer = User.objects.create_user(
            username="lifecycle_customer",
            password="test-password",
        )

        self.staff = User.objects.create_user(
            username="lifecycle_staff",
            password="test-password",
            is_staff=True,
        )

        permission = Permission.objects.get(
            codename="approve_cardorder",
            content_type__app_label="cards",
        )
        self.staff.user_permissions.add(permission)

        self.account = Account.objects.create(
            user=self.customer,
            account_number="9876543210",
        )

    def create_order(self, card_format):
        return CardOrder.objects.create(
            account=self.account,
            card_type="debit",
            card_format=card_format,
            cardholder_name="Test Customer",
            delivery_address="Test Street" if card_format == "physical" else "",
            status="pending",
        )

    def test_virtual_card_issues_without_staff_approval(self):
        order = self.create_order("virtual")

        card = issue_virtual_card_order(order.pk)

        order.refresh_from_db()

        self.assertIsInstance(card, BankCard)
        self.assertEqual(order.status, "issued")
        self.assertEqual(order.bank_card_id, card.pk)
        self.assertTrue(card.online_enabled)

    def test_virtual_card_duplicate_request_returns_same_card(self):
        order = self.create_order("virtual")

        first = issue_virtual_card_order(order.pk)
        second = issue_virtual_card_order(order.pk)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            BankCard.objects.filter(account=self.account).count(),
            1,
        )

    def test_virtual_order_cannot_use_physical_approval(self):
        order = self.create_order("virtual")

        with self.assertRaises(CardIssuanceError):
            approve_physical_card_order(order.pk, self.staff)

    def test_physical_issuance_creates_delivery_record(self):
        order = self.create_order("physical")

        card = approve_physical_card_order(
            order.pk,
            self.staff,
        )

        self.assertTrue(
            CardDelivery.objects.filter(order=order).exists()
        )

        self.assertEqual(card.status, "active")

    def test_delivery_can_be_dispatched_and_delivered(self):
        order = self.create_order("physical")

        approve_physical_card_order(order.pk, self.staff)

        delivery = update_card_delivery(
            order.pk,
            "dispatched",
            tracking_number="KLOSA-TRACK-001",
            courier="Klosa Logistics",
        )

        self.assertEqual(delivery.status, "dispatched")
        self.assertIsNotNone(delivery.dispatched_at)

        delivery = update_card_delivery(
            order.pk,
            "in_transit",
        )

        self.assertEqual(delivery.status, "in_transit")

        delivery = update_card_delivery(
            order.pk,
            "delivered",
        )

        self.assertEqual(delivery.status, "delivered")
        self.assertIsNotNone(delivery.delivered_at)

    def test_delivery_cannot_skip_dispatch(self):
        order = self.create_order("physical")

        approve_physical_card_order(order.pk, self.staff)

        with self.assertRaises(CardIssuanceError):
            update_card_delivery(order.pk, "delivered")

    def test_delivery_cannot_be_updated_for_virtual_card(self):
        order = self.create_order("virtual")

        issue_virtual_card_order(order.pk)

        with self.assertRaises(CardIssuanceError):
            update_card_delivery(order.pk, "dispatched")
