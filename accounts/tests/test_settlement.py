from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from accounts.credit_settlement import decide, propose
from accounts.models import Account, CreditAccount

User = get_user_model()
_n = iter(range(1, 10**6))


def staff(*codes):
    s = User.objects.create_user(f"s{next(_n)}", password="x", is_staff=True)
    s.user_permissions.add(*Permission.objects.filter(codename__in=codes))
    return User.objects.get(pk=s.pk)


class SettlementTests(TestCase):
    def setUp(self):
        u = User.objects.create_user("cust", password="x")
        self.acc = Account.objects.create(user=u, account_number="1000000001", balance=Decimal("500"))
        self.credit = CreditAccount.objects.create(account=self.acc, credit_limit=Decimal("100000"),
                                                   outstanding_balance=Decimal("40000"), status="active")
        self.officer, self.approver = staff("propose_creditsettlement"), staff("approve_creditsettlement")

    def make(self, amount="25000"):
        return propose(self.officer, self.credit.pk, "asset_trade", amount, "Gold bar sold", "RCPT-1")

    def owed(self):
        self.credit.refresh_from_db()
        return self.credit.outstanding_balance

    def test_approval_restores_credit_and_leaves_the_balance_alone(self):
        s = self.make()
        decide(self.approver, s.pk, True)
        self.acc.refresh_from_db()
        self.credit.refresh_from_db()
        self.assertEqual((self.owed(), self.acc.balance, self.credit.available_credit),
                         (Decimal("15000"), Decimal("500"), Decimal("85000")))

    def test_it_cannot_be_decided_twice_or_by_the_recorder(self):
        s = self.make()
        both = staff("propose_creditsettlement", "approve_creditsettlement")
        s2 = propose(both, self.credit.pk, "external", "1000", "Bank payment", "REF-9")
        with self.assertRaises(PermissionDenied):
            decide(both, s2.pk, True)
        decide(self.approver, s.pk, True)
        with self.assertRaises(ValidationError):
            decide(self.approver, s.pk, True)
        self.assertEqual(self.owed(), Decimal("15000"))

    def test_rejecting_needs_a_reason_and_changes_nothing(self):
        s = self.make()
        with self.assertRaises(ValidationError):
            decide(self.approver, s.pk, False, " ")
        decide(self.approver, s.pk, False, "Receipt unreadable")
        self.assertEqual(self.owed(), Decimal("40000"))

    def test_limits_permissions_and_bad_input(self):
        self.make("30000")
        with self.assertRaises(ValidationError):
            self.make("10001")                                   # 30,000 waiting plus 10,001 is over 40,000
        for bad in ("0", "-5", "abc", "10.999"):
            with self.assertRaises(ValidationError, msg=bad):
                self.make(bad)
        with self.assertRaises(PermissionDenied):
            propose(self.approver, self.credit.pk, "external", "1", "x y z", "abc")
        with self.assertRaises(PermissionDenied):
            decide(self.officer, 1, True)