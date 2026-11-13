
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse

from beneficiary.models import Beneficiary

User = get_user_model()


class BeneficiaryTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="adeola",
            password="testpass123"
        )

        self.client.login(
            username="adeola",
            password="testpass123"
        )

        self.beneficiary = Beneficiary.objects.create(
            owner=self.user,
            name="Chinedu Okafor",
            account_number="0123456789",
            bank_name="GTBank",
            bank_code="058",
            is_active=True,
        )

    def test_beneficiary_is_created(self):
        self.assertEqual(
            Beneficiary.objects.count(), 1
        )

    def test_user_can_view_dashboard(self):
        response = self.client.get(
            reverse("accounts:home")
        )

        self.assertEqual(response.status_code, 200)

    def test_beneficiary_belongs_to_correct_user(self):
        self.assertEqual(
            self.beneficiary.owner,
            self.user
        )