from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(DEBUG=True, SMS_BACKEND="console")
class SignupCodeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.data = {"phone": "08012345678", "password": "Str0ng!pass-93", "agree": "on"}

    def test_the_code_is_shown_in_test_mode_and_works(self):
        self.client.post(reverse("accounts:signup"), self.data)
        code = self.client.get(reverse("accounts:verify_phone")).context["dev_code"]
        self.assertRegex(code, r"^\d{6}$")
        r = self.client.post(reverse("accounts:verify_phone"), {"code": code})
        self.assertRedirects(r, reverse("accounts:onboarding"), fetch_redirect_response=False)

    @override_settings(DEBUG=False)
    def test_the_code_is_never_shown_when_debug_is_off(self):
        self.client.post(reverse("accounts:signup"), self.data)
        self.assertEqual(self.client.get(reverse("accounts:verify_phone")).context["dev_code"], "")

    def test_resend_sends_one_new_code_a_minute(self):
        with mock.patch("accounts.services.deliver_sms") as sms:
            self.client.post(reverse("accounts:signup"), self.data)
            self.client.post(reverse("accounts:resend_code"))
            self.client.post(reverse("accounts:resend_code"))
        self.assertEqual(sms.call_count, 2)

    def test_resend_without_a_signup_goes_to_signup(self):
        r = self.client.post(reverse("accounts:resend_code"))
        self.assertRedirects(r, reverse("accounts:signup"), fetch_redirect_response=False)

    def test_an_sms_failure_does_not_break_signup(self):
        with mock.patch("accounts.services.deliver_sms", side_effect=RuntimeError("provider down")):
            r = self.client.post(reverse("accounts:signup"), self.data)
        self.assertRedirects(r, reverse("accounts:verify_phone"), fetch_redirect_response=False)