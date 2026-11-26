from unittest import mock

from django.test import TestCase, override_settings

from .. import service as fa
from .helpers import SETTINGS, make_user


@override_settings(**SETTINGS)
class NetworkTests(TestCase):
    def setUp(self):
        self.u = make_user("net")

    def _req(self, ip):
        return mock.Mock(META={"REMOTE_ADDR": ip})

    def _login_from(self, ip):
        from backoffice.models import SecurityEvent
        SecurityEvent.objects.create(user=self.u, event="login", ip=ip)

    def test_network_id(self):
        self.assertEqual(fa.network_id("203.0.113.77"), "203.0.113.0/24")
        self.assertEqual(fa.network_id("2001:db8:1:2:3:4:5:6"), "2001:db8:1:2::/64")
        self.assertEqual(fa.network_id("::ffff:203.0.113.77"), "203.0.113.0/24")   # mapped IPv4

    def test_network_id_bad_input(self):
        for bad in ("", None, "garbage", "999.1.1.1", "  ", "1.2.3"):
            self.assertIsNone(fa.network_id(bad), bad)

    def test_no_history_means_no_check(self):
        self.assertFalse(fa.new_network(self.u, self._req("1.2.3.4")))

    def test_same_network_not_new(self):
        self._login_from("1.2.3.10")
        self.assertFalse(fa.new_network(self.u, self._req("1.2.3.200")))

    def test_different_network_is_new(self):
        self._login_from("1.2.3.10")
        self.assertTrue(fa.new_network(self.u, self._req("9.9.9.9")))

    def test_unreadable_request_ip_never_triggers(self):
        self._login_from("1.2.3.10")
        self.assertFalse(fa.new_network(self.u, self._req("")))
        self.assertFalse(fa.new_network(self.u, None))

  