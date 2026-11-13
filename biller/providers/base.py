from dataclasses import dataclass


@dataclass
class Result:
    status: str                 # "success", "failed" or "pending"
    provider_reference: str = ""
    token: str = ""
    message: str = ""


class BaseProvider:
    """Wrap one real provider here. Use order.reference as the provider's request ID,
    so a repeated call can never buy twice."""

    def price_of(self, category, provider_code, product_code):
        return None             # return a Decimal, or None if the product is unknown

    def verify_customer(self, category, provider_code, product_code, customer_ref):
        return None             # the customer's name, so the user can confirm before paying

    def purchase(self, order):
        raise NotImplementedError

    def requery(self, order):
        raise NotImplementedError
        
    def catalog(self, category):
        """What the screens show: {"providers": [{"code", "name"}], "products": {code: [{"code", "name", "price"}]}}.
        Prices are for display only. price_of() decides what is charged."""
        return {"providers": [], "products": {}}