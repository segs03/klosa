from decimal import Decimal

# Placeholder limits. Check them against your provider and your regulator.
RULES = {
    "airtime":     {"min": Decimal("50"),  "max": Decimal("50000"),  "ref": "phone", "product": False, "tier": 0},
    "data":        {"min": Decimal("50"),  "max": Decimal("100000"), "ref": "phone", "product": True,  "tier": 0},
    "education":   {"min": Decimal("500"), "max": Decimal("500000"), "ref": "id",    "product": True,  "tier": 0},
    "electricity": {"min": Decimal("500"), "max": Decimal("500000"), "ref": "id",    "product": False, "tier": 0},
    "tv":          {"min": Decimal("500"), "max": Decimal("200000"), "ref": "id",    "product": True,  "tier": 0},
    "internet":    {"min": Decimal("500"), "max": Decimal("200000"), "ref": "id",    "product": True,  "tier": 0},
    "other":       {"min": Decimal("100"), "max": Decimal("200000"), "ref": "id",    "product": False, "tier": 0},
    "betting":     {"min": Decimal("100"), "max": Decimal("100000"), "ref": "id",    "product": False, "tier": 1,
                    "daily": Decimal("50000")},
}

# for these, the provider sets the price. The browser's amount is ignored.
FIXED_PRICE = {"data", "education", "tv", "internet"}

FEES = {}          # category -> Decimal fee. Empty means no fee.