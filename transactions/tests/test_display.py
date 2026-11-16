from django.test import SimpleTestCase

from transactions.models import Transaction


class DisplayTests(SimpleTestCase):
    def tx(self, kind):
        return Transaction(transaction_type=kind)

    def test_direction_and_category(self):
        cases = {
            "deposit": (True, "income", False),
            "withdrawal": (False, "expense", False),
            "payment": (False, "expense", False),
            "transfer": (False, "expense", False),
            "savings_deposit": (False, "other", True),       # money leaves the main balance
            "savings_withdrawal": (True, "other", True),      # money comes back
        }
        for kind, (credit, cat, savings) in cases.items():
            t = self.tx(kind)
            self.assertEqual((t.is_credit, t.category, t.is_savings), (credit, cat, savings), kind)

    def test_every_type_has_an_icon(self):
        for kind, _ in Transaction.TRANSACTION_TYPES:
            self.assertTrue(self.tx(kind).icon)
            
            
    def test_refund_is_a_credit_but_not_income(self):
        t = Transaction(transaction_type="deposit", description="Refund: Airtime failed")
        self.assertEqual((t.is_credit, t.category, t.kind, t.label), (True, "other", "refund", "Refund"))