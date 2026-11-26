import io
import json
from unittest import mock
from urllib.error import HTTPError, URLError

from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from accounts import services, sms_infobip as t

URLOPEN = "accounts.sms_infobip.urlopen"
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CFG = dict(CACHES=LOCMEM, INFOBIP_API_KEY="IBsecretkey", INFOBIP_BASE_URL="https://abc12.api.infobip.com",
           INFOBIP_SENDER="447491163443", INFOBIP_DAILY_CAP=500)


def ok(group=1):
    r = mock.MagicMock()
    r.__enter__.return_value = r
    r.read.return_value = json.dumps({"messages": [{"messageId": "m1",
                                      "status": {"groupId": group, "name": "PENDING"}}]}).encode()
    return r


def posted(u):
    req = u.call_args[0][0]
    return req, json.loads(req.data)


@override_settings(**CFG)
class SendTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_posts_the_expected_request(self):
        with mock.patch(URLOPEN, return_value=ok()) as u:
            self.assertEqual(t.send("08012345678", "Klosa code: 123456"), "m1")
        req, body = posted(u)
        self.assertEqual(req.full_url, "https://abc12.api.infobip.com/sms/2/text/advanced")
        self.assertEqual(req.get_header("Authorization"), "App IBsecretkey")
        self.assertEqual(body, {"messages": [{"destinations": [{"to": "2348012345678"}],
                                              "text": "Klosa code: 123456", "from": "447491163443"}]})

    def test_phone_formats(self):
        for given, want in [("08012345678", "2348012345678"), ("+2348012345678", "2348012345678"),
                            ("8012345678", "2348012345678"), ("0801 234 5678", "2348012345678")]:
            self.assertEqual(t.to_msisdn(given), want)

    def test_unusable_numbers_never_reach_the_network(self):
        for bad in ("", None, "abc", "123", "0801", "1" * 20):
            with mock.patch(URLOPEN) as u:
                with self.assertRaises(ValueError):
                    t.send(bad, "x")
            u.assert_not_called()

    def test_http_error_logs_without_secrets(self):
        err = HTTPError("https://x", 401, "no", {}, io.BytesIO(b'{"text": "Unauthorized"}'))
        with mock.patch(URLOPEN, side_effect=err):
            with self.assertLogs("sms", "ERROR") as logs:
                with self.assertRaises(HTTPError):
                    t.send("08012345678", "Klosa code: 654321")
        text = "\n".join(logs.output)
        for secret in ("IBsecretkey", "654321", "08012345678", "2348012345678"):
            self.assertNotIn(secret, text)

    def test_rejected_status_is_a_failure(self):
        with mock.patch(URLOPEN, return_value=ok(group=5)):
            with self.assertLogs("sms", "ERROR"):
                with self.assertRaises(RuntimeError):
                    t.send("08012345678", "x")

    def test_unreadable_reply_is_a_failure(self):
        r = ok()
        r.read.return_value = b"<html>gateway error</html>"
        with mock.patch(URLOPEN, return_value=r):
            with self.assertLogs("sms", "ERROR"):
                with self.assertRaises(RuntimeError):
                    t.send("08012345678", "x")

    def test_network_failure_raises(self):
        with mock.patch(URLOPEN, side_effect=URLError("no route")):
            with self.assertLogs("sms", "ERROR"):
                with self.assertRaises(URLError):
                    t.send("08012345678", "x")

    @override_settings(INFOBIP_API_KEY="")
    def test_missing_key_fails_loudly(self):
        with self.assertRaises(ImproperlyConfigured):
            t.send("08012345678", "x")

    @override_settings(INFOBIP_DAILY_CAP=2)
    def test_daily_cap(self):
        with mock.patch(URLOPEN, return_value=ok()) as u:
            t.send("08012345678", "1")
            t.send("08012345679", "2")
            with self.assertLogs("sms", "ERROR"):
                with self.assertRaises(RuntimeError):
                    t.send("08012345670", "3")
        self.assertEqual(u.call_count, 2)

    def test_plugs_into_deliver_sms(self):
        with override_settings(SMS_BACKEND="accounts.sms_infobip.send"), mock.patch(URLOPEN, return_value=ok()) as u:
            services.deliver_sms("08012345678", "Klosa Bank code: 111111")
        self.assertEqual(posted(u)[1]["messages"][0]["text"], "Klosa Bank code: 111111")
