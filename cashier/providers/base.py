from dataclasses import dataclass
from decimal import Decimal


@dataclass
class Result:
    status: str                      # "success", "failed" or "pending"
    provider_reference: str = ""
    amount: Decimal | None = None
    message: str = ""


class BaseCashProvider:
    """Wrap one real provider here. Use our reference as the provider's request ID, so a repeated
    call can never pay twice. Card details must stay on the provider's own checkout page."""

    def start_checkout(self, deposit):
        raise NotImplementedError          # return (checkout_url, provider_reference)

    def verify_deposit(self, deposit):
        raise NotImplementedError          # return Result with the amount really paid

    def name_enquiry(self, bank_code, account_number):
        return None                        # the account holder's name, or None

    def payout(self, withdrawal):
        raise NotImplementedError

    def requery_payout(self, withdrawal):
        raise NotImplementedError