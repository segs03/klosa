import hashlib
import hmac
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import BankCard, CardProviderEvent
from .services import authorize_purchase, refund_purchase


def signature_ok(body, header):
    secret = getattr(settings, "CARDS_WEBHOOK_SECRET", "")
    if not secret or not header:                           # no secret set: reject everything
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.strip().lower())


@csrf_exempt
@require_POST
def webhook(request):
    body = request.body
    if len(body) > 20000:
        return HttpResponse(status=413)
    if not signature_ok(body, request.headers.get("X-Signature", "")):
        return HttpResponse(status=401)
    try:
        data = json.loads(body)
        kind = str(data["type"])
        event_id = str(data["event_id"])[:150]
        provider = str(data.get("provider", "mock"))[:50]
        CardProviderEvent.objects.get_or_create(provider_name=provider, event_id=event_id,
                                                defaults={"event_type": kind[:100]})
        if kind == "authorization":
            card = BankCard.objects.filter(provider_card_id=str(data["card_id"])).first()
            if card is None:
                return JsonResponse({"approved": False, "reason": "Unknown card"})
            ok, why = authorize_purchase(
                card, data["amount"], str(data["provider_transaction_id"])[:100], data.get("merchant", ""),
                data.get("channel", "pos"), data.get("currency", "NGN"))
            result = {"approved": ok, "reason": why}
        elif kind in ("refund", "reversal"):
            amount = data.get("amount") if kind == "refund" else None
            result = {"ok": True, "applied": refund_purchase(str(data["original_transaction_id"])[:100], amount)}
        else:
            return HttpResponse(status=400)
    except (ValueError, KeyError, TypeError, ValidationError):
        return HttpResponse(status=400)
    CardProviderEvent.objects.filter(provider_name=provider, event_id=event_id).update(
        processed=True, processed_at=timezone.now())
    return JsonResponse(result)