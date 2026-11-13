from decimal import Decimal

from .base import BaseProvider, Result

NETWORKS = [("mtn", "MTN"), ("airtel", "Airtel"), ("glo", "Glo"), ("9mobile", "9mobile")]
PLANS = [("plan-1gb", "1 GB · 30 days", "500"), ("plan-2gb", "2 GB · 30 days", "1000"),
         ("plan-5gb", "5 GB · 30 days", "2500")]


def _prod(rows):
    return [{"code": c, "name": n, "price": Decimal(p)} for c, n, p in rows]


CATALOG = {
    "airtime": {"providers": [{"code": c, "name": n} for c, n in NETWORKS], "products": {}},
    "data": {"providers": [{"code": c, "name": n} for c, n in NETWORKS],
             "products": {c: _prod(PLANS) for c, _ in NETWORKS}},
    "education": {"providers": [{"code": "exam", "name": "Exam pins (demo)"}],
                  "products": {"exam": _prod([("exam-pin", "Result checker PIN", "3500"),
                                              ("exam-form", "Registration form", "8000")])}},
    "electricity": {"providers": [{"code": "ikedc", "name": "Ikeja Electric (demo)"},
                                  {"code": "ekedc", "name": "Eko Electric (demo)"}], "products": {}},
    "tv": {"providers": [{"code": "tvco", "name": "TV bouquets (demo)"}],
           "products": {"tvco": _prod([("basic", "Basic bouquet", "2000"), ("premium", "Premium bouquet", "6000")])}},
    "internet": {"providers": [{"code": "netco", "name": "Home internet (demo)"}],
                 "products": {"netco": _prod([("10mbps", "10 Mbps · 30 days", "10000")])}},
    "betting": {"providers": [{"code": "betco", "name": "Betting wallet (demo)"}], "products": {}},
}


class MockProvider(BaseProvider):
    """Rules by the end of the customer reference:
    0000 fails, 9999 stays pending then succeeds, 8888 stays pending then fails, others succeed."""
    calls = 0

    def catalog(self, category):
        return CATALOG.get(category, {"providers": [], "products": {}})

    def price_of(self, category, provider_code, product_code):
        for p in self.catalog(category)["products"].get(provider_code, []):
            if p["code"] == product_code:
                return p["price"]
        return None

    def verify_customer(self, category, provider_code, product_code, customer_ref):
        return "Test Customer"

    def purchase(self, order):
        type(self).calls += 1
        ref = order.customer_ref
        if ref.endswith("0000"):
            return Result("failed", message="Provider declined")
        if ref.endswith("9999") or ref.endswith("8888"):
            return Result("pending")
        return Result("success", provider_reference="MOCK-" + order.reference)

    def requery(self, order):
        ref = order.customer_ref
        if ref.endswith("9999"):
            return Result("success", provider_reference="MOCK-" + order.reference)
        if ref.endswith("8888"):
            return Result("failed", message="Provider declined")
        return Result("pending")