import io
import json
from unittest import mock
from urllib.error import HTTPError, URLError

from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMessage, EmailMultiAlternatives, send_mail
from django.test import SimpleTestCase, override_settings

from accounts import resend_backend as rb

URLOPEN = "accounts.resend_backend.urlopen"


def ok():
    r = mock.MagicMock()
    r.__enter__.return_value = r
    r.read.return_value = b'{"id": "abc"}'
    return r


def rejected(code=403, body=b'{"message": "The domain is not verified"}'):
    return HTTPError(rb.API_URL, code, "err", {}, io.BytesIO(body))


def msg(**kw):
    d = dict(subject="Subj", body="Body", from_email="Klosa <no-reply@k.com>", to=["a@x.com"])
    d.update(kw)
    return EmailMessage(**d)


def sent_json(urlopen):
    return json.loads(urlopen.call_args[0][0].data)


@override_settings(RESEND_API_KEY="re_secret", EMAIL_TIMEOUT=10)
class ResendBackendTests(SimpleTestCase):
    def test_posts_json_with_key_and_user_agent(self):
        with mock.patch(URLOPEN, return_value=ok()) as u:
            self.assertEqual(rb.ResendBackend().send_messages([msg()]), 1)
        req = u.call_args[0][0]
        self.assertEqual(req.full_url, rb.API_URL)
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Authorization"), "Bearer re_secret")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertTrue(req.get_header("User-agent"))
        self.assertEqual(u.call_args[1]["timeout"], 10)
        self.assertEqual(json.loads(req.data), {
            "from": "Klosa <no-reply@k.com>", "to": ["a@x.com"], "subject": "Subj", "text": "Body"})

    def test_html_alternative(self):
        m = EmailMultiAlternatives("S", "plain", "no-reply@k.com", ["a@x.com"])
        m.attach_alternative("<b>rich</b>", "text/html")
        with mock.patch(URLOPEN, return_value=ok()) as u:
            rb.ResendBackend().send_messages([m])
        body = sent_json(u)
        self.assertEqual((body["text"], body["html"]), ("plain", "<b>rich</b>"))

    def test_html_only_message(self):
        m = msg(body="<p>hi</p>")
        m.content_subtype = "html"
        with mock.patch(URLOPEN, return_value=ok()) as u:
            rb.ResendBackend().send_messages([m])
        body = sent_json(u)
        self.assertEqual(body["html"], "<p>hi</p>")
        self.assertNotIn("text", body)

    def test_recipients_cc_bcc_reply_to(self):
        m = msg(to=["a@x.com", "b@x.com"], cc=["c@x.com"], bcc=["d@x.com"], reply_to=["r@x.com"])
        with mock.patch(URLOPEN, return_value=ok()) as u:
            rb.ResendBackend().send_messages([m])
        body = sent_json(u)
        self.assertEqual((body["to"], body["cc"], body["bcc"], body["reply_to"]),
                         (["a@x.com", "b@x.com"], ["c@x.com"], ["d@x.com"], ["r@x.com"]))

    def test_bcc_only_is_sent_as_the_recipient(self):
        with mock.patch(URLOPEN, return_value=ok()) as u:
            rb.ResendBackend().send_messages([msg(to=[], bcc=["d@x.com"])])
        body = sent_json(u)
        self.assertEqual(body["to"], ["d@x.com"])
        self.assertNotIn("bcc", body)

    def test_no_recipients_is_an_error(self):
        with mock.patch(URLOPEN) as u:
            with self.assertRaises(ValueError):
                rb.ResendBackend().send_messages([msg(to=[])])
        u.assert_not_called()

    def test_attachments_are_refused_not_dropped(self):
        m = msg()
        m.attach("a.txt", "x", "text/plain")
        with mock.patch(URLOPEN) as u:
            with self.assertRaises(ValueError):
                rb.ResendBackend().send_messages([m])
        u.assert_not_called()

    def test_rejection_raises_and_logs_the_reason_but_never_the_key(self):
        with mock.patch(URLOPEN, side_effect=rejected()):
            with self.assertLogs("email", "ERROR") as logs:
                with self.assertRaises(HTTPError):
                    rb.ResendBackend().send_messages([msg()])
        text = "\n".join(logs.output)
        self.assertIn("domain is not verified", text)
        self.assertNotIn("re_secret", text)

    def test_network_failure_raises(self):
        with mock.patch(URLOPEN, side_effect=URLError("no route")):
            with self.assertLogs("email", "ERROR"):
                with self.assertRaises(URLError):
                    rb.ResendBackend().send_messages([msg()])

    def test_timeout_raises(self):
        with mock.patch(URLOPEN, side_effect=TimeoutError("slow")):
            with self.assertRaises(TimeoutError):
                rb.ResendBackend().send_messages([msg()])

    def test_fail_silently_returns_zero(self):
        with mock.patch(URLOPEN, side_effect=rejected(500, b"boom")):
            with self.assertLogs("email", "ERROR"):
                self.assertEqual(rb.ResendBackend(fail_silently=True).send_messages([msg()]), 0)

    def test_one_failure_does_not_stop_the_rest(self):
        with mock.patch(URLOPEN, side_effect=[rejected(), ok()]):
            with self.assertLogs("email", "ERROR"):
                n = rb.ResendBackend(fail_silently=True).send_messages([msg(), msg(to=["b@x.com"])])
        self.assertEqual(n, 1)

    def test_django_send_mail_goes_through_it(self):
        with override_settings(EMAIL_BACKEND="accounts.resend_backend.ResendBackend"), \
                mock.patch(URLOPEN, return_value=ok()) as u:
            self.assertEqual(send_mail("S", "B", "no-reply@k.com", ["a@x.com"]), 1)
        u.assert_called_once()


class ConfigTests(SimpleTestCase):
    @override_settings(RESEND_API_KEY="")
    def test_missing_key_fails_loudly(self):
        with self.assertRaises(ImproperlyConfigured):
            rb.ResendBackend()

    def test_unset_key_fails_loudly(self):
        with override_settings():
            from django.conf import settings
            if hasattr(settings, "RESEND_API_KEY"):
                del settings.RESEND_API_KEY
            with self.assertRaises(ImproperlyConfigured):
                rb.ResendBackend()
