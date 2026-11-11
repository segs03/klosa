import re
from django.test import SimpleTestCase, TestCase, Client
from accounts.utils import normalize_phone
from unittest import mock

from django.core.cache import cache
from django.urls import reverse
from django.contrib.auth import get_user_model
from accounts.models import Account



User = get_user_model()



class PhoneNormalizeTests(SimpleTestCase):
    def test_valid_formats(self):
        for raw in ["08012345678", "8012345678", "+2348012345678",
                    "234 801 234 5678", "0801-234-5678"]:
            self.assertEqual(normalize_phone(raw), "+2348012345678", raw)

    def test_invalid(self):
        for raw in ["", "12345", "abcdefghijk", "0601234567", "0801234567", "080123456789"]:
            self.assertIsNone(normalize_phone(raw), raw)


@mock.patch("accounts.services.deliver_sms")
class PhoneSignupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse("accounts:signup")
        self.data = {"phone": "08012345678", "password": "Str0ng!pass-93", "agree": "on"}

    def code(self, sms):
        return re.search(r"\b(\d{6})\b", sms.call_args[0][1]).group(1)

    def verify(self, sms, code=None):
        return self.client.post(reverse("accounts:verify_phone"), {"code": code or self.code(sms)})

    def test_signup_sends_code_and_waits_for_verification(self, sms):
        r = self.client.post(self.url, self.data)
        self.assertRedirects(r, reverse("accounts:verify_phone"))
        u = User.objects.get(phone_number="+2348012345678")
        self.assertFalse(u.is_active)
        self.assertFalse(Account.objects.filter(user=u).exists())
        sms.assert_called_once()

    def test_verify_creates_account_and_logs_in(self, sms):
        self.client.post(self.url, self.data)
        r = self.verify(sms)
        self.assertRedirects(r, reverse("accounts:onboarding"), fetch_redirect_response=False)
        u = User.objects.get(phone_number="+2348012345678")
        self.assertTrue(u.phone_verified)
        self.assertFalse(u.is_staff)
        acc = Account.objects.get(user=u)
        self.assertEqual(acc.kyc_tier, 0)
        self.assertEqual(len(acc.account_number), 10)

    def test_wrong_code_does_not_activate(self, sms):
        self.client.post(self.url, self.data)
        self.verify(sms, code="000000")
        self.assertFalse(User.objects.get(phone_number="+2348012345678").is_active)

    def test_code_locks_after_five_wrong_tries(self, sms):
        self.client.post(self.url, self.data)
        good = self.code(sms)
        for _ in range(5):
            self.verify(sms, code="000000")
        self.verify(sms, code=good)
        self.assertFalse(User.objects.get(phone_number="+2348012345678").is_active)

    def test_verified_number_gets_no_sms_and_no_second_account(self, sms):
        self.client.post(self.url, self.data)
        self.verify(sms)
        sms.reset_mock()
        c = Client()
        r = c.post(self.url, self.data)
        self.assertRedirects(r, reverse("accounts:verify_phone"))
        sms.assert_not_called()
        self.assertEqual(User.objects.filter(phone_number="+2348012345678").count(), 1)

    def test_same_number_in_other_formats_is_the_same_account(self, sms):
        self.client.post(self.url, self.data)
        self.verify(sms)
        Client().post(self.url, {**self.data, "phone": "+234 801 234 5678"})
        self.assertEqual(User.objects.filter(phone_number="+2348012345678").count(), 1)

    def test_unverified_signup_is_replaced(self, sms):
        self.client.post(self.url, self.data)
        Client().post(self.url, {**self.data, "password": "An0ther!pass-77"})
        self.assertEqual(User.objects.filter(phone_number="+2348012345678").count(), 1)
        self.assertEqual(sms.call_count, 2)

    def test_sms_limit_per_number(self, sms):
        for _ in range(5):
            Client().post(self.url, self.data)
        self.assertEqual(sms.call_count, 3)

    def test_invalid_phone_and_weak_password_rejected(self, sms):
        self.client.post(self.url, {**self.data, "phone": "12345"})
        self.client.post(self.url, {**self.data, "password": "12345678"})
        self.assertFalse(User.objects.exists())
        sms.assert_not_called()

    def test_cannot_self_assign_staff(self, sms):
        self.client.post(self.url, {**self.data, "is_staff": "on", "is_superuser": "on"})
        self.verify(sms)
        u = User.objects.get(phone_number="+2348012345678")
        self.assertFalse(u.is_staff)
        self.assertFalse(u.is_superuser)


class IdentifierLoginTests(TestCase):
    def setUp(self):
        self.cust = User.objects.create_user("u1", password="Str0ng!pass-93", phone_number="+2348012345678",
                                                          phone_verified=True, email="c@x.com")
        self.staff = User.objects.create_user("t@bank.com", password="Str0ng!pass-93",
                                              email="T@Bank.com", is_staff=True)

    def test_customer_logs_in_with_phone_in_any_format(self):
        for ident in ["08012345678", "+2348012345678", "0801 234 5678"]:
            self.assertTrue(Client().login(username=ident, password="Str0ng!pass-93"), ident)

    def test_wrong_password(self):
        self.assertFalse(Client().login(username="08012345678", password="nope"))

    def test_staff_logs_in_with_email(self):
        self.assertTrue(Client().login(username="t@bank.com", password="Str0ng!pass-93"))
        self.assertTrue(Client().login(username="T@BANK.COM", password="Str0ng!pass-93"))

    def test_customer_cannot_log_in_with_email(self):
        self.assertFalse(Client().login(username="c@x.com", password="Str0ng!pass-93"))

    def test_staff_cannot_log_in_with_a_phone(self):
        User.objects.filter(pk=self.staff.pk).update(phone_number="+2349011111111")
        self.assertFalse(Client().login(username="09011111111", password="Str0ng!pass-93"))