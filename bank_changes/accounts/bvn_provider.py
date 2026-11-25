"""Thin adapter around the BVN lookup provider (standard library only, no extra install).

Only this file knows the provider's API. Everything else gets a plain dict.
The URL, auth header and response keys below are PLACEHOLDERS: adapt the two
marked sections to your provider (Dojah, Prembly, Smile ID, ...).

Never log the BVN or the response body.
"""
import json
import logging
from datetime import datetime
from urllib import error, parse, request

from django.conf import settings

log = logging.getLogger("kyc.bvn")

_DATE_FORMATS = ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y")


class BVNNotFound(Exception):
    pass


class ProviderError(Exception):
    pass


def _parse_date(value):
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime((value or "").strip(), fmt).date()
        except ValueError:
            continue
    raise ProviderError("Unreadable date of birth from provider.")


def lookup(bvn):
    """Return {"first_name", "middle_name", "last_name", "date_of_birth": date, "phone"}."""
    # ADAPT 1: endpoint and auth
    req = request.Request(
        f"{settings.BVN_API_URL}?{parse.urlencode({'bvn': bvn})}",
        headers={"Authorization": f"Bearer {settings.BVN_API_KEY}", "Accept": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as e:                      # must come before URLError
        if e.code in (404, 422):                      # ADAPT: how your provider says "no such BVN"
            raise BVNNotFound()
        log.warning("BVN provider returned status %s", e.code)
        raise ProviderError("bad status")
    except (error.URLError, TimeoutError, OSError):
        log.warning("BVN provider unreachable")
        raise ProviderError("unreachable")
    except ValueError:
        log.warning("BVN provider response not understood")
        raise ProviderError("bad payload")

    try:
        # ADAPT 2: response shape
        e = payload["entity"]
        return {
            "first_name": (e.get("first_name") or "").strip(),
            "middle_name": (e.get("middle_name") or "").strip(),
            "last_name": (e.get("last_name") or "").strip(),
            "date_of_birth": _parse_date(e.get("date_of_birth")),
            "phone": (e.get("phone_number1") or "").strip(),
        }
    except (KeyError, TypeError, AttributeError):
        log.warning("BVN provider response not understood")
        raise ProviderError("bad payload")
