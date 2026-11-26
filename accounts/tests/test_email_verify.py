import re
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import email_verify as ev
from accounts.models import Account
from accounts.tiers import refresh_tier

from .test_tiers import approved_address, doc

User = get_user_model()
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}   # never flush a real Redis
PIN = dict(CACHES=LOCMEM, KYC_EMAIL_REQUIRED=True, KYC_ADDRESS_REQUIRED=True)


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.u = User.objects.create_user("ann", password="pass12345")
        self.acc = Account.objects.create(user=self.u, account_number="1000000001")
        self.other = User.objects.create_user("bob", password="pass12345")

    def code(self):
        return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

    def wrong(self):
        return "000000" if self.code() != "000000" else "111111"

    def fresh(self):
        self.u.refresh_from_db()
        return self.u

    def send(self, email="ann@example.com"):
        with mock.patch.object(ev, "hit", return_value=False):      # skip the cooldown unless a test wants it
            ev.send_code(self.u, email)


@override_settings(**PIN)
class ServiceTests(Base):
    def test_correct_code_verifies_and_saves_the_email(self):
        self.send()
        self.assertTrue(ev.check_code(self.u, self.code()))
        u = self.fresh()
        self.assertEqual(u.email, "ann@example.com")
        self.assertIsNotNone(u.email_verified_at)

    def test_email_is_trimmed_and_lowercased(self):
        self.send("  Ann@Example.COM ")
        ev.check_code(self.u, self.code())
        self.assertEqual(self.fresh().email, "ann@example.com")

    def test_wrong_code_changes_nothing(self):
        self.send()
        self.assertFalse(ev.check_code(self.u, self.wrong()))
        self.assertIsNone(self.fresh().email_verified_at)

    def test_garbage_codes(self):
        self.send()
        for bad in ("", None, "abc", "12345", "1234567", " "):
            self.assertFalse(ev.check_code(self.u, bad), bad)

    def test_no_code_requested(self):
        self.assertFalse(ev.check_code(self.u, "123456"))

    def test_expired_code(self):
        self.send()
        c = self.code()
        cache.clear()                                               # what the cache does after 10 minutes
        self.assertFalse(ev.check_code(self.u, c))

    def test_five_wrong_guesses_burn_the_code(self):
        self.send()
        c, bad = self.code(), self.wrong()
        for _ in range(ev.MAX_TRIES):
            self.assertFalse(ev.check_code(self.u, bad))
        self.assertFalse(ev.check_code(self.u, c))                  # even the right code is now dead
        self.assertIsNone(self.fresh().email_verified_at)

    def test_code_works_once(self):
        self.send()
        c = self.code()
        self.assertTrue(ev.check_code(self.u, c))
        self.assertFalse(ev.check_code(self.u, c))

    def test_a_code_belongs_to_one_user(self):
        self.send()
        self.assertFalse(ev.check_code(self.other, self.code()))

    def test_a_new_email_kills_the_old_code(self):
        self.send("a@example.com")
        old = self.code()
        self.send("b@example.com")
        self.assertFalse(ev.check_code(self.u, old))
        self.assertTrue(ev.check_code(self.u, self.code()))
        self.assertEqual(self.fresh().email, "b@example.com")

    def test_cooldown_blocks_a_second_send(self):
        ev.send_code(self.u, "a@example.com")
        with self.assertRaises(ValidationError):
            ev.send_code(self.u, "a@example.com")
        self.assertEqual(len(mail.outbox), 1)

    def test_hourly_limit(self):
        with mock.patch.object(ev, "hit", side_effect=[False, True]):
            with self.assertRaises(ValidationError):
                ev.send_code(self.u, "a@example.com")
        self.assertEqual(len(mail.outbox), 0)

    def test_address_verified_by_someone_else_is_refused(self):
        get_user_model().objects.filter(pk=self.other.pk).update(
            email="x@example.com", email_verified_at=timezone.now())
        with self.assertRaises(ValidationError):
            self.send("X@Example.com")
        self.assertEqual(len(mail.outbox), 0)

    def test_unverified_use_of_an_address_does_not_block(self):
        get_user_model().objects.filter(pk=self.other.pk).update(email="x@example.com")
        self.send("x@example.com")
        self.assertEqual(len(mail.outbox), 1)

    def test_someone_else_verifies_it_between_send_and_check(self):
        self.send("x@example.com")
        c = self.code()
        get_user_model().objects.filter(pk=self.other.pk).update(
            email="x@example.com", email_verified_at=timezone.now())
        self.assertFalse(ev.check_code(self.u, c))
        self.assertIsNone(self.fresh().email_verified_at)

    def test_mail_failure_is_clean_and_leaves_no_code(self):
        with mock.patch.object(ev, "send_mail", side_effect=OSError("smtp down")), \
                mock.patch.object(ev, "hit", return_value=False):
            with self.assertRaises(ValidationError):
                ev.send_code(self.u, "a@example.com")
        self.assertFalse(ev.check_code(self.u, "123456"))

    def test_the_code_is_not_stored_in_the_clear(self):
        self.send()
        stored = cache.get(ev._k("code", self.u))
        self.assertNotIn(self.code(), str(stored))

    def test_changing_the_email_drops_verification(self):
        self.send()
        ev.check_code(self.u, self.code())
        u = self.fresh()
        u.email = "new@example.com"
        u.save()
        self.assertIsNone(self.fresh().email_verified_at)

    def test_same_email_in_other_case_keeps_verification(self):
        self.send()
        ev.check_code(self.u, self.code())
        u = self.fresh()
        u.email = u.email.upper()
        u.save()
        self.assertIsNotNone(self.fresh().email_verified_at)

    def test_unrelated_saves_keep_verification(self):
        self.send()
        ev.check_code(self.u, self.code())
        u = self.fresh()
        u.first_name = "Ann"
        u.save()
        self.assertIsNotNone(self.fresh().email_verified_at)


@override_settings(**PIN)
class TierTests(Base):
    """Tier 2 = BVN + verified email. Tier 3 = also a second ID, an address and the face check."""

    def tier(self):
        self.acc.refresh_from_db()
        return self.acc.tier

    def verify_email(self):
        self.send()
        self.assertTrue(ev.check_code(self.u, self.code()))         # check_code refreshes the tier itself

    def rest_of_tier_three(self):
        doc(self.u, "nin")
        approved_address(self.u)
        Account.objects.filter(pk=self.acc.pk).update(face_verified_at=timezone.now())

    def test_bvn_alone_is_not_enough_for_tier_two(self):
        doc(self.u, "bvn")
        refresh_tier(self.u)
        self.assertEqual(self.tier(), 1)

    def test_bvn_then_email_makes_tier_two(self):
        doc(self.u, "bvn")
        refresh_tier(self.u)
        self.verify_email()
        self.assertEqual(self.tier(), 2)

    def test_email_then_bvn_also_makes_tier_two(self):
        self.verify_email()
        self.assertEqual(self.tier(), 1)                            # no BVN yet
        doc(self.u, "bvn")
        refresh_tier(self.u)
        self.assertEqual(self.tier(), 2)

    def test_email_alone_changes_nothing(self):
        self.verify_email()
        self.assertEqual(self.tier(), 1)

    def test_tier_three_needs_the_rest_on_top(self):
        doc(self.u, "bvn")
        self.verify_email()
        self.assertEqual(self.tier(), 2)
        self.rest_of_tier_three()
        refresh_tier(self.u)
        self.assertEqual(self.tier(), 3)

    def test_everything_else_without_the_email_stays_tier_one(self):
        doc(self.u, "bvn")
        self.rest_of_tier_three()
        refresh_tier(self.u)
        self.assertEqual(self.tier(), 1)

    def test_losing_the_email_does_not_lower_the_tier(self):
        doc(self.u, "bvn")
        self.verify_email()
        u = self.fresh()
        u.email = "other@example.com"
        u.save()                                                    # drops the verification
        refresh_tier(self.u)
        self.assertEqual(self.tier(), 2)

    @override_settings(KYC_EMAIL_REQUIRED=False)
    def test_rule_can_be_switched_off(self):
        doc(self.u, "bvn")
        refresh_tier(self.u)
        self.assertEqual(self.tier(), 2)


@override_settings(**PIN)
class ViewTests(Base):
    def setUp(self):
        super().setUp()
        self.c = Client()
        self.c.login(username="ann", password="pass12345")

    def test_login_required(self):
        r = Client().get(reverse("accounts:email"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r["Location"])
        self.assertEqual(Client().post(reverse("accounts:email_verify"), {"code": "123456"}).status_code, 302)

    def test_start_sends_a_code_and_moves_on(self):
        r = self.c.post(reverse("accounts:email"), {"email": "ann@example.com"})
        self.assertRedirects(r, reverse("accounts:email_verify"), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ann@example.com"])

    def test_bad_address_sends_nothing(self):
        for bad in ("", "nope", "a@", "@b.com"):
            r = self.c.post(reverse("accounts:email"), {"email": bad})
            self.assertEqual(r.status_code, 200, bad)
        self.assertEqual(len(mail.outbox), 0)

    def test_wrong_code_shows_an_error_and_verifies_nothing(self):
        self.c.post(reverse("accounts:email"), {"email": "ann@example.com"})
        r = self.c.post(reverse("accounts:email_verify"), {"code": self.wrong()})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(self.fresh().email_verified_at)

    def test_right_code_verifies_and_returns_to_the_upgrade_page(self):
        self.c.post(reverse("accounts:email"), {"email": "ann@example.com"})
        r = self.c.post(reverse("accounts:email_verify"), {"code": self.code()})
        self.assertRedirects(r, reverse("accounts:upgrade"), fetch_redirect_response=False)
        self.assertIsNotNone(self.fresh().email_verified_at)

    def test_already_verified_skips_the_form(self):
        get_user_model().objects.filter(pk=self.u.pk).update(email="ann@example.com", email_verified_at=timezone.now())
        r = self.c.get(reverse("accounts:email"))
        self.assertRedirects(r, reverse("accounts:upgrade"), fetch_redirect_response=False)

    def test_verify_page_is_throttled(self):
        self.c.post(reverse("accounts:email"), {"email": "ann@example.com"})
        codes = [self.c.post(reverse("accounts:email_verify"), {"code": self.wrong()}).status_code for _ in range(20)]
        self.assertEqual(codes[-1], 429)

    def test_upgrade_page_asks_for_the_email_right_after_the_bvn(self):
        doc(self.u, "bvn")
        r = self.c.get(reverse("accounts:upgrade"))
        self.assertEqual(r.context["nxt"][1], reverse("accounts:email"))
        self.assertIs(dict(r.context["checks"])["Email"], False)

    def test_after_the_email_it_asks_for_the_second_id(self):
        doc(self.u, "bvn")
        get_user_model().objects.filter(pk=self.u.pk).update(email="ann@example.com", email_verified_at=timezone.now())
        r = self.c.get(reverse("accounts:upgrade"))
        self.assertEqual(r.context["nxt"][1], reverse("accounts:verify"))
        self.assertIs(dict(r.context["checks"])["Email"], True)
