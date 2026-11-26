import io
import json
from unittest import mock
from urllib.error import HTTPError, URLError

from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from accounts import services, sms_termii as t



URLOPEN = "accounts.sms_termii.urlopen"
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}   # never touch a real Redis
CFG = dict(CACHES=LOCMEM, TERMII_API_KEY="TLsecretkey", TERMII_BASE_URL="https://v4.api.termii.com",
           TERMII_SENDER_ID="N-Alert", TERMII_CHANNEL="dnd", TERMII_DAILY_CAP=500)


def ok(body=None):
    if body is None:
        body = {"code": "ok", "message_id": "m1", "message": "Successfully Sent"}
    r = mock.MagicMock()
    r.__enter__.return_value = r
    r.read.return_value = json.dumps(body).encode()
    return r


def rejected(code=400, body=b'{"message": "Insufficient balance"}'):
    return HTTPError("https://x", code, "err", {}, io.BytesIO(body))


def posted(urlopen):
    req = urlopen.call_args[0][0]
    return req, json.loads(req.data)


@override_settings(**CFG)
class SendTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_posts_the_expected_request(self):
        with mock.patch(URLOPEN, return_value=ok()) as u:
            self.assertEqual(t.send("08012345678", "Klosa code: 123456"), "m1")
        req, body = posted(u)
        self.assertEqual(req.full_url, "https://v4.api.termii.com/api/sms/send")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertTrue(req.get_header("User-agent"))
        self.assertEqual(body, {"api_key": "TLsecretkey", "to": "2348012345678", "from": "N-Alert",
                                "sms": "Klosa code: 123456", "type": "plain", "channel": "dnd"})
        self.assertEqual(u.call_args[1]["timeout"], 10)

    def test_phone_formats(self):
        for given, want in [("08012345678", "2348012345678"), ("+2348012345678", "2348012345678"),
                            ("2348012345678", "2348012345678"), ("8012345678", "2348012345678"),
                            ("0801 234 5678", "2348012345678")]:
            self.assertEqual(t.to_msisdn(given), want, given)

    def test_unusable_numbers_never_reach_the_network(self):
        for bad in ("", None, "abc", "123", "0801", "1" * 20):
            with mock.patch(URLOPEN) as u:
                with self.assertRaises(ValueError):
                    t.send(bad, "x")
            u.assert_not_called()

    @override_settings(TERMII_BASE_URL="https://v4.api.termii.com/api/")
    def test_base_url_with_a_trailing_api_still_works(self):
        with mock.patch(URLOPEN, return_value=ok()) as u:
            t.send("08012345678", "x")
        self.assertEqual(posted(u)[0].full_url, "https://v4.api.termii.com/api/sms/send")

    @override_settings(TERMII_CHANNEL="generic", TERMII_SENDER_ID="Klosa")
    def test_channel_and_sender_come_from_settings(self):
        with mock.patch(URLOPEN, return_value=ok()) as u:
            t.send("08012345678", "x")
        body = posted(u)[1]
        self.assertEqual((body["channel"], body["from"]), ("generic", "Klosa"))

    def test_http_error_raises_and_logs_the_reason_without_secrets(self):
        with mock.patch(URLOPEN, side_effect=rejected()):
            with self.assertLogs("sms", "ERROR") as logs:
                with self.assertRaises(HTTPError):
                    t.send("08012345678", "Klosa code: 654321")
        text = "\n".join(logs.output)
        self.assertIn("Insufficient balance", text)
        for secret in ("TLsecretkey", "654321", "08012345678", "2348012345678"):
            self.assertNotIn(secret, text)

    def test_a_200_that_is_not_ok_is_a_failure(self):
        for body in ({"code": "error", "message": "bad"}, {}, {"message": "Successfully Sent"}):
            with mock.patch(URLOPEN, return_value=ok(body)):
                with self.assertLogs("sms", "ERROR"):
                    with self.assertRaises(RuntimeError):
                        t.send("08012345678", "x")

    def test_non_json_reply_is_a_failure(self):
        r = ok()
        r.read.return_value = b"<html>gateway error</html>"
        with mock.patch(URLOPEN, return_value=r):
            with self.assertLogs("sms", "ERROR"):
                with self.assertRaises(RuntimeError):
                    t.send("08012345678", "x")

    def test_network_failures_raise(self):
        with mock.patch(URLOPEN, side_effect=URLError("no route")):
            with self.assertLogs("sms", "ERROR"):
                with self.assertRaises(URLError):
                    t.send("08012345678", "x")
        with mock.patch(URLOPEN, side_effect=TimeoutError("slow")):
            with self.assertRaises(TimeoutError):
                t.send("08012345678", "x")

    @override_settings(TERMII_API_KEY="")
    def test_missing_key_fails_loudly(self):
        with self.assertRaises(ImproperlyConfigured):
            t.send("08012345678", "x")

    @override_settings(TERMII_BASE_URL="")
    def test_missing_base_url_fails_loudly(self):
        with self.assertRaises(ImproperlyConfigured):
            t.send("08012345678", "x")

    @override_settings(TERMII_DAILY_CAP=2)
    def test_daily_cap_stops_a_runaway_bill(self):
        with mock.patch(URLOPEN, return_value=ok()) as u:
            t.send("08012345678", "1")
            t.send("08012345679", "2")
            with self.assertLogs("sms", "ERROR"):
                with self.assertRaises(RuntimeError):
                    t.send("08012345670", "3")
        self.assertEqual(u.call_count, 2)

    def test_plugs_into_deliver_sms(self):
        with override_settings(SMS_BACKEND="accounts.sms_termii.send"), mock.patch(URLOPEN, return_value=ok()) as u:
            services.deliver_sms("08012345678", "Klosa Bank code: 111111")
        self.assertEqual(posted(u)[1]["sms"], "Klosa Bank code: 111111")
