from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Account
from beneficiary.models import Beneficiary
from transactions.models import Transaction

User = get_user_model()

SEND_URL_NAME = "transfers:send"      # change if your send route has another name


def make(name, number, balance="0", first=""):
    u = User.objects.create_user(name, password="pass12345", first_name=first)
    a = Account.objects.create(user=u, account_number=number, balance=Decimal(balance))
    a.set_pin("4827")
    return u, a


@override_settings(KLOSA_BANK_CODE="999", ALLOW_EXTERNAL_TRANSFERS=False)
class SendRecipientTests(TestCase):
    def setUp(self):
        cache.clear()
        self.ua, self.a = make("a", "1000000001", "10000")
        self.ub, self.b = make("b", "1000000002")
        self.uc, self.c = make("c", "1000000003")

        def mk(owner, num, code):
            return Beneficiary.objects.create(
                owner=owner, name="N", account_number=num, bank_code=code, bank_name="Bank")

        self.internal = mk(self.ua, "1000000002", "999")
        self.external = mk(self.ua, "1000000002", "058")     # same digits, another bank
        self.foreign = mk(self.uc, "1000000002", "999")      # belongs to another user
        self.url = reverse(SEND_URL_NAME)
        self.client.login(username="a", password="pass12345")

    def send(self, ben, amount="1000"):
        token = self.client.get(self.url).context["token"]
        return self.client.post(self.url, {
            "beneficiary": ben.pk, "amount": amount, "pin": "4827",
            "token": token, "description": ""})

    def bal(self, acc):
        acc.refresh_from_db()
        return acc.balance

    def test_internal_transfer_credits_recipient(self):
        self.send(self.internal)
        self.assertEqual(self.bal(self.a), Decimal("9000"))
        self.assertEqual(self.bal(self.b), Decimal("1000"))

    def test_other_bank_with_same_digits_is_not_credited_or_debited(self):
        self.send(self.external)
        self.assertEqual(self.bal(self.a), Decimal("10000"))
        self.assertEqual(self.bal(self.b), Decimal("0"))

    def test_closed_recipient_is_refused(self):
        Account.objects.filter(pk=self.b.pk).update(closed_at=timezone.now())
        self.send(self.internal)
        self.assertEqual(self.bal(self.a), Decimal("10000"))
        self.assertEqual(self.bal(self.b), Decimal("0"))

    def test_cannot_use_another_users_beneficiary(self):
        self.send(self.foreign)
        self.assertEqual(self.bal(self.a), Decimal("10000"))
        self.assertFalse(Transaction.objects.exists())

    def test_counterparty_is_saved_on_the_transaction(self):
        if not any(f.name == "counterparty_name" for f in Transaction._meta.get_fields()):
            self.skipTest("counterparty fields are not on Transaction yet")
        User.objects.filter(pk=self.ub.pk).update(first_name="Bob", last_name="Smith")
        self.send(self.internal)
        tx = Transaction.objects.get(account=self.a, transaction_type="transfer")
        self.assertEqual(tx.counterparty_name, "Bob Smith")
        self.assertEqual(tx.counterparty_account, "1000000002")

@override_settings(KLOSA_BANK_CODE="999")
class BeneficiaryViewTests(TestCase):
    def setUp(self):
        self.ua, self.a = make("a", "1000000001")
        self.ub, self.b = make("b", "1000000002", first="Bob Smith")
        self.client.login(username="a", password="pass12345")
        self.add = reverse("beneficiary:add")

    def post(self, **kw):
        data = {"name": "Typed", "bank_name": "Klosa Bank", "account_number": "1000000002"}
        data.update(kw)
        return self.client.post(self.add, data)

    def test_klosa_beneficiary_takes_the_real_holder_name(self):
        self.post()
        self.assertEqual(Beneficiary.objects.get(owner=self.ua).name, "Bob Smith")

    def test_other_bank_beneficiary_is_saved(self):
        self.post(bank_name="Access Bank", account_number="0123456789")
        b = Beneficiary.objects.get(owner=self.ua)
        self.assertEqual(b.bank_code, "044")

    def test_cannot_add_own_account(self):
        self.post(account_number="1000000001")
        self.assertFalse(Beneficiary.objects.exists())

    def test_unknown_or_closed_klosa_account_is_refused(self):
        self.post(account_number="1999999999")
        Account.objects.filter(pk=self.b.pk).update(closed_at=timezone.now())
        self.post()
        self.assertFalse(Beneficiary.objects.exists())

    def test_bad_account_number_and_bank(self):
        for bad in ({"account_number": "123"}, {"account_number": "12345abcde"},
                    {"bank_name": ""}, {"bank_name": "Made Up Bank"}):
            self.post(**bad)
        self.assertFalse(Beneficiary.objects.exists())

    def test_other_bank_needs_a_name(self):
        self.post(bank_name="Access Bank", name="")
        self.assertFalse(Beneficiary.objects.exists())

    def test_user_cannot_pick_a_bank_code_directly(self):
        self.post(bank_name="Access Bank", bank_code="999", account_number="0123456789")
        self.assertEqual(Beneficiary.objects.get(owner=self.ua).bank_code, "044")

    def test_duplicate_is_refused(self):
        self.post()
        self.post()
        self.assertEqual(Beneficiary.objects.filter(owner=self.ua).count(), 1)

    def test_removed_beneficiary_can_be_added_again(self):
        self.post()
        b = Beneficiary.objects.get(owner=self.ua)
        self.client.post(reverse("beneficiary:delete", args=[b.pk]))
        b.refresh_from_db()
        self.assertFalse(b.is_active)
        self.post()
        b.refresh_from_db()
        self.assertTrue(b.is_active)
        self.assertEqual(Beneficiary.objects.filter(owner=self.ua).count(), 1)

    def test_cannot_remove_someone_elses_beneficiary(self):
        other = Beneficiary.objects.create(
            owner=self.ub, name="X", account_number="1000000009", bank_code="058", bank_name="Other")
        r = self.client.post(reverse("beneficiary:delete", args=[other.pk]))
        self.assertEqual(r.status_code, 404)
        other.refresh_from_db()
        self.assertTrue(other.is_active)

    def test_delete_needs_post(self):
        self.post()
        b = Beneficiary.objects.get(owner=self.ua)
        self.assertEqual(self.client.get(reverse("beneficiary:delete", args=[b.pk])).status_code, 405)
