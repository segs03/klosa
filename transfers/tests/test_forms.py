from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Account
from beneficiary.models import Beneficiary
from transfers.forms import RequestForm, TransferForm

User = get_user_model()


@override_settings(KLOSA_BANK_CODE="999")
class TransferFormTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("ann", password="x")
        self.acc = Account.objects.create(user=self.u, account_number="1000000001", balance=Decimal("100000"))
        self.ben = Beneficiary.objects.create(owner=self.u, name="B", account_number="1000000002",
                                              bank_code="999", bank_name="Klosa Bank")

    def form(self, used="0", **kw):
        data = {"beneficiary": self.ben.pk, "amount": "1000", "pin": "4827"}
        data.update(kw)
        return TransferForm(data, account=self.acc, used_today=Decimal(used))

    def test_valid_form(self):
        self.assertTrue(self.form().is_valid())

    def test_needs_exactly_one_recipient(self):
        self.assertFalse(self.form(beneficiary="").is_valid())
        self.assertFalse(self.form(account_number="1000000002").is_valid())

    def test_own_account_is_refused(self):
        self.assertFalse(self.form(beneficiary="", account_number="1000000001").is_valid())

    def test_closed_account_does_not_crash_and_is_refused(self):
        self.acc.closed_at = timezone.now()
        self.assertFalse(self.form().is_valid())

    def test_tier_zero_limits(self):
        self.assertFalse(self.form(amount="20001").is_valid())
        self.assertFalse(self.form(amount="1000", used="49500").is_valid())

    def test_insufficient_balance(self):
        self.acc.balance = Decimal("500")
        self.assertFalse(self.form().is_valid())

    def test_other_bank_beneficiary_is_refused(self):
        other = Beneficiary.objects.create(owner=self.u, name="X", account_number="1000000009",
                                           bank_code="058", bank_name="GTBank")
        self.assertFalse(self.form(beneficiary=other.pk).is_valid())

    def test_bad_pin_formats(self):
        for pin in ("12", "12345", "abcd", "١٢٣٤"):
            self.assertFalse(self.form(pin=pin).is_valid(), pin)

    def test_bad_account_numbers(self):
        for n in ("123", "12345abcde", "12345678901"):
            self.assertFalse(self.form(beneficiary="", account_number=n).is_valid(), n)

    def test_cannot_use_a_beneficiary_that_belongs_to_someone_else(self):
        o = User.objects.create_user("eve", password="x")
        foreign = Beneficiary.objects.create(owner=o, name="E", account_number="1000000003",
                                             bank_code="999", bank_name="Klosa Bank")
        self.assertFalse(self.form(beneficiary=foreign.pk).is_valid())


class RequestFormTests(TestCase):
    def test_valid_and_invalid(self):
        self.assertTrue(RequestForm({"account_number": "1000000002", "amount": "50", "note": "hi"}).is_valid())
        for bad in ({"account_number": "123"}, {"amount": "0"}, {"amount": "-5"}, {"amount": "abc"}):
            data = {"account_number": "1000000002", "amount": "50"}
            data.update(bad)
            self.assertFalse(RequestForm(data).is_valid(), bad)

    def test_note_is_tidied_and_length_limited(self):
        f = RequestForm({"account_number": "1000000002", "amount": "5", "note": "  a   b "})
        self.assertTrue(f.is_valid())
        self.assertEqual(f.cleaned_data["note"], "a b")
        self.assertFalse(RequestForm({"account_number": "1000000002", "amount": "5", "note": "x" * 101}).is_valid())

    def test_there_is_no_currency_field(self):
        self.assertNotIn("currency", RequestForm().fields)