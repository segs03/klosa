import secrets
import uuid

from django.utils import timezone


class MockIssuer:
    """Stands in for a real card issuer. A real one returns the same few fields."""
    name = "mock"

    def issue(self, order):
        today = timezone.localdate()
        return {"provider_name": self.name, "provider_card_id": "MOCK-" + uuid.uuid4().hex,
                "last_four": f"{secrets.randbelow(10000):04d}",
                "expiry_month": today.month, "expiry_year": today.year + 4}