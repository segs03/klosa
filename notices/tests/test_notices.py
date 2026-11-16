from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Account, CreditAccount, CreditApplication, KYCApplication, KYCReview, VerifiedDocument
from cards.models import CardOrder
from notices.models import Notification
from notices.services import notify_user
from transactions.models import CreditTransaction, Transaction
from transfers.models import MoneyRequest

User = get_user_model()
_n = iter(range(1, 10**6))


def customer(first="Ann"):
    n = next(_n)
    u = User.objects.create_user(f"u{n}", password="pass12345", first_name=first)
    return u, Account.objects.create(user=u, account_number=str(1000000000 + n))


def titles(u):
    return list(Notification.objects.filter(user=u).values_list("title", flat=True))


def notice(u, title):
    return Notification.objects.filter(user=u, title=title).first()


class MoneyAlertTests(TestCase):
    def setUp(self):
        self.u, self.acc = customer()

    def tx(self, kind, amount="5000", desc=""):
        return Transaction.objects.create(account=self.acc, transaction_type=kind, amount=Decimal(amount), description=desc)

    def test_money_in_is_a_credit_alert(self):
        self.tx("deposit", "5000", "Transfer from Ben")
        n = notice(self.u, "Credit alert")
        self.assertIn("₦5,000.00", n.body)
        self.assertIn("Transfer from Ben", n.body)
        self.assertEqual(n.level, "success")

    def test_money_out_is_a_debit_alert(self):
        for kind in ("transfer", "payment", "withdrawal"):
            self.tx(kind)
        self.assertEqual(titles(self.u).count("Debit alert"), 3)

    def test_refunds_and_the_welcome_gift_have_their_own_wording(self):
        self.tx("deposit", "300", "Refund: Airtime failed")
        self.tx("deposit", "200000", "Welcome bonus")
        self.assertIn("Refund received", titles(self.u))
        self.assertIn("Welcome gift", titles(self.u))

    def test_savings_moves_are_quiet_except_maturity(self):
        self.tx("savings_deposit", "1000", "Moved to savings: Rent")
        self.tx("savings_withdrawal", "500", "Moved from savings: Rent")
        self.assertEqual(titles(self.u), [])
        self.tx("savings_withdrawal", "1050", "Savings matured: Rent")
        self.assertEqual(titles(self.u), ["Savings matured"])

    def test_a_rolled_back_action_leaves_no_notice(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.tx("deposit")
                raise RuntimeError("boom")
        self.assertEqual(titles(self.u), [])


class StatusNoticeTests(TestCase):
    def setUp(self):
        self.u, self.acc = customer()
        self.other, self.other_acc = customer("Ben")

    def kyc(self, doc="nin"):
        return KYCApplication.objects.create(user=self.u, country_code="NG", document_type=doc, legal_name="Ada Obi",
                                             date_of_birth=date(1990, 1, 1), identity_document="kyc/x.jpg",
                                             identity_digest=f"{doc}{self.u.pk}".ljust(64, "0"))

    def test_id_received_rejected_with_the_reason(self):
        app = self.kyc()
        self.assertIn("ID received", titles(self.u))
        KYCReview.objects.create(application=app, reviewer=self.other, decision="rejected", reason="Photo is blurry")
        app.status = "rejected"
        app.save(update_fields=["status"])
        n = notice(self.u, "We could not verify your ID")
        self.assertEqual(n.body, "Photo is blurry")
        self.assertEqual(n.level, "warning")

    @override_settings(WELCOME_BONUS_ENABLED=True)
    def test_id_verified_nudges_towards_the_other_id(self):
        app = self.kyc("nin")
        VerifiedDocument.objects.create(user=self.u, document_type="nin", identity_digest=app.identity_digest,
                                        verified_at=timezone.now(), source_application=app)
        app.status = "verified"
        app.save(update_fields=["status"])
        self.assertIn("BVN", notice(self.u, "ID verified").body)

    def test_credit_request_and_approval(self):
        a = CreditApplication.objects.create(user=self.u, requested_amount=Decimal("100000"))
        self.assertIn("Credit request received", titles(self.u))
        a.assessed_amount = Decimal("80000")
        a.status = "approved"
        a.save(update_fields=["assessed_amount", "status"])
        self.assertIn("80,000", notice(self.u, "Credit approved").body)
        b = CreditApplication.objects.create(user=self.other, requested_amount=Decimal("5000"))
        b.decision_reason = "Too low an income"
        b.status = "rejected"
        b.save(update_fields=["decision_reason", "status"])
        self.assertEqual(notice(self.other, "Credit request declined").body, "Too low an income")

    def test_card_order_steps(self):
        o = CardOrder.objects.create(account=self.acc, card_type="debit", card_format="virtual", cardholder_name="ANN")
        for status, title in (("approved", "Card request approved"), ("issued", "Your card is ready")):
            o.status = status
            o.save(update_fields=["status"])
            self.assertIn(title, titles(self.u))
        self.assertIn("Card request received", titles(self.u))

    def test_money_requests_notify_the_right_person(self):
        r = MoneyRequest.objects.create(requester=self.u, recipient=self.other, amount=Decimal("500"), note="lunch")
        n = notice(self.other, "Money request")
        self.assertIn("lunch", n.body)
        self.assertEqual(titles(self.u), [])
        r.status = "declined"
        r.save(update_fields=["status"])
        self.assertIn("Request declined", titles(self.u))

    def test_three_wrong_pins_send_one_lock_alert(self):
        self.acc.set_pin("4827")
        for _ in range(3):
            self.acc.check_pin("0000")
        self.assertEqual(titles(self.u).count("Transaction PIN locked"), 1)

    def test_card_purchases_show_the_available_credit(self):
        credit = CreditAccount.objects.create(account=self.acc, credit_limit=Decimal("100000"), status="active")
        credit.outstanding_balance = Decimal("1000")
        credit.save()
        CreditTransaction.objects.create(credit_account=credit, transaction_type="purchase", amount=Decimal("1000"),
                                         reference="R1", description="Shop")
        n = notice(self.u, "Card purchase")
        self.assertIn("Shop", n.body)
        self.assertIn("99,000", n.body)

    def test_a_blocked_card_and_a_suspended_credit_line_are_alerts(self):
        from cards.models import BankCard
        card = BankCard.objects.create(account=self.acc, cardholder_name="ANN", last_four="1234", expiry_month=1, expiry_year=2035)
        card.status = "blocked"
        card.save()
        credit = CreditAccount.objects.create(account=self.acc, credit_limit=Decimal("1"), status="active")
        credit.status = "suspended"
        credit.save()
        self.assertTrue({"Card blocked", "Credit suspended"} <= set(titles(self.u)))


class ServiceTests(TestCase):
    def setUp(self):
        self.u, _ = customer()

    def test_a_keyed_notice_is_sent_once(self):
        notify_user(self.u, "Hello", key="hello")
        notify_user(self.u, "Hello again", key="hello")
        self.assertEqual(Notification.objects.filter(user=self.u).count(), 1)

    def test_links_must_stay_inside_the_app(self):
        for bad in ("https://evil.com", "//evil.com", "javascript:alert(1)"):
            self.assertEqual(notify_user(self.u, "x", link=bad).link, "")
        self.assertEqual(notify_user(self.u, "x", link="/accounts/").link, "/accounts/")

    def test_text_is_trimmed_and_the_level_is_safe(self):
        n = notify_user(self.u, "T" * 200, "B" * 500, level="weird")
        self.assertEqual((len(n.title), len(n.body), n.level), (80, 240, "info"))


class InboxTests(TestCase):
    def setUp(self):
        cache.clear()
        self.u, self.acc = customer()
        self.other, _ = customer("Ben")
        self.mine = notify_user(self.u, "Mine", "hello", link="/accounts/")
        self.theirs = notify_user(self.other, "Theirs")
        self.client.login(username=self.u.username, password="pass12345")

    def test_login_is_required(self):
        self.assertEqual(Client().get(reverse("notices:list")).status_code, 302)

    def test_only_my_notices_are_listed_with_the_unread_count(self):
        r = self.client.get(reverse("notices:list"))
        self.assertContains(r, "Mine")
        self.assertNotContains(r, "Theirs")
        self.assertEqual(r.context["unread_count"], 1)

    def test_opening_marks_it_read_and_follows_the_link(self):
        r = self.client.get(reverse("notices:open", args=[self.mine.pk]))
        self.assertRedirects(r, "/accounts/", fetch_redirect_response=False)
        self.mine.refresh_from_db()
        self.assertIsNotNone(self.mine.read_at)
        self.assertEqual(self.client.get(reverse("notices:list")).context["unread_count"], 0)

    def test_a_notice_without_a_link_goes_back_to_the_inbox(self):
        n = notify_user(self.u, "No link")
        self.assertRedirects(self.client.get(reverse("notices:open", args=[n.pk])), reverse("notices:list"))

    def test_someone_elses_notice_is_a_404(self):
        self.assertEqual(self.client.get(reverse("notices:open", args=[self.theirs.pk])).status_code, 404)

    def test_mark_all_read_needs_post_and_only_touches_mine(self):
        self.assertEqual(self.client.get(reverse("notices:read_all")).status_code, 405)
        self.client.post(reverse("notices:read_all"))
        self.mine.refresh_from_db()
        self.theirs.refresh_from_db()
        self.assertIsNotNone(self.mine.read_at)
        self.assertIsNone(self.theirs.read_at)


@override_settings(BILLER_PROVIDER="biller.providers.mock.MockProvider")
class FlashMessageTests(TestCase):
    """These fail until `{% include "includes/toasts.html" %}` is in base.html."""

    def test_a_message_shows_once_on_the_page_you_land_on(self):
        u, acc = customer()
        Account.objects.filter(pk=acc.pk).update(balance=Decimal("100000"))
        acc.refresh_from_db()
        acc.set_pin("4827")
        c = Client()
        c.login(username=u.username, password="pass12345")
        url = reverse("biller:service", args=["airtime"])
        c.get(url)
        r = c.post(url, {"provider": "mtn", "customer_ref": "08010001234", "amount": "1000", "step": "pay",
                         "pin": "4827", "token": c.session["bp_token"]}, follow=True)
        self.assertContains(r, 'class="kt kt-success"')
        self.assertEqual(r.content.decode().count("Payment successful."), 1)     # twice means an old loop is still there