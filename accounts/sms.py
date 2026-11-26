import requests
from django.conf import settings

def _call(path, payload):
    r = requests.post(
        f"{settings.INFOBIP_BASE_URL}{path}",
        json=payload,
        headers={"Authorization": f"App {settings.INFOBIP_API_KEY}"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()

def send_pin(phone):  # phone like 2349132681864
    data = _call("/2fa/2/pin", {
        "applicationId": settings.INFOBIP_2FA_APP_ID,
        "messageId": settings.INFOBIP_2FA_MESSAGE_ID,
        "from": settings.INFOBIP_SENDER,
        "to": phone,
    })
    return data["pinId"]  # store in session

def verify_pin(pin_id, pin):
    return _call(f"/2fa/2/pin/{pin_id}/verify", {"pin": pin}).get("verified", False)