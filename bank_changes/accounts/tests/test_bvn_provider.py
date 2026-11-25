import json
from datetime import date
from unittest import mock
from urllib import error

from django.test import SimpleTestCase, override_settings

from accounts import bvn_provider
from accounts.bvn_provider import BVNNotFound, ProviderError

GOOD = {"entity": {"first_name": "Ada", "middle_name": "Grace", "last_name": "Obi",
                   "date_of_birth": "1990-05-17", "phone_number1": "08012345678"}}


def _resp(payload):
    r = mock.MagicMock()
    r.read.return_value = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    r.__enter__.return_value = r
    return r


@override_settings(BVN_API_URL="https://example.test/bvn", BVN_API_KEY="key123")
class ProviderTests(SimpleTestCase):
    def call(self, **kw):
        with mock.patch("accounts.bvn_provider.request.urlopen", **kw) as m:
            try:
                return bvn_provider.lookup("12345678901"), m
            finally:
                self.last = m

    def test_reads_a_good_response(self):
        data, _ = self.call(return_value=_resp(GOOD))
        self.assertEqual(data["first_name"], "Ada")
        self.assertEqual(data["last_name"], "Obi")
        self.assertEqual(data["date_of_birth"], date(1990, 5, 17))
        self.assertEqual(data["phone"], "08012345678")

    def test_sends_the_key_and_number(self):
        _, m = self.call(return_value=_resp(GOOD))
        req = m.call_args[0][0]
        self.assertIn("bvn=12345678901", req.full_url)
        self.assertEqual(req.get_header("Authorization"), "Bearer key123")

    def test_a_missing_bvn_is_not_found(self):
        with self.assertRaises(BVNNotFound):
            self.call(side_effect=error.HTTPError("u", 404, "nf", {}, None))

    def test_other_http_errors_are_provider_errors(self):
        with self.assertRaises(ProviderError):
            self.call(side_effect=error.HTTPError("u", 500, "boom", {}, None))

    def test_network_failures_are_provider_errors(self):
        for exc in (error.URLError("down"), TimeoutError(), OSError("reset")):
            with self.subTest(exc=type(exc).__name__), self.assertRaises(ProviderError):
                self.call(side_effect=exc)

    def test_garbage_is_a_provider_error(self):
        for payload in (b"not json", {}, {"entity": None}, {"entity": {"date_of_birth": "soon"}}):
            with self.subTest(payload=payload), self.assertRaises(ProviderError):
                self.call(return_value=_resp(payload))

    def test_common_date_formats_are_read(self):
        for text in ("1990-05-17", "17-May-1990", "17-05-1990", "17/05/1990"):
            entity = dict(GOOD["entity"], date_of_birth=text)
            with self.subTest(text=text):
                data, _ = self.call(return_value=_resp({"entity": entity}))
                self.assertEqual(data["date_of_birth"], date(1990, 5, 17))
