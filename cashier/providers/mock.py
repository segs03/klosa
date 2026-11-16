from .base import BaseCashProvider, Result


class MockCashProvider(BaseCashProvider):
    """Withdrawal rules by the end of the account number:
    0000 fails, 9999 stays pending then succeeds, 8888 stays pending then fails, 1111 is not a real account."""
    calls = 0
    paid = {}                              # deposit reference -> amount, set by the fake payment page

    def start_checkout(self, deposit):
        from django.urls import reverse
        return reverse("cashier:mockpay", args=[deposit.reference]), "MOCKCO-" + deposit.reference

    def verify_deposit(self, deposit):
        amount = type(self).paid.get(deposit.reference)
        if amount is None:
            return Result("pending")
        return Result("success", provider_reference="MOCK-" + deposit.reference, amount=amount)

    def name_enquiry(self, bank_code, account_number):
        return None if account_number.endswith("1111") else "Test Account Holder"

    def payout(self, w):
        type(self).calls += 1
        n = w.account_number
        if n.endswith("0000"):
            return Result("failed", message="Bank declined")
        if n.endswith("9999") or n.endswith("8888"):
            return Result("pending")
        return Result("success", provider_reference="MOCKPO-" + w.reference)

    def requery_payout(self, w):
        n = w.account_number
        if n.endswith("9999"):
            return Result("success", provider_reference="MOCKPO-" + w.reference)
        if n.endswith("8888"):
            return Result("failed", message="Bank declined")
        return Result("pending")