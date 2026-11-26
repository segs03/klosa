from cryptography.fernet import Fernet, InvalidToken
from django.test import TestCase, override_settings

from .. import vault
from .helpers import SETTINGS


@override_settings(**SETTINGS)
class VaultTests(TestCase):
    def test_roundtrip(self):
        self.assertEqual(vault.unseal(vault.seal(b"template")), b"template")

    def test_sealed_data_is_not_plaintext(self):
        self.assertNotIn(b"template", vault.seal(b"template"))

    def test_tampered_token_rejected(self):
        t = bytearray(vault.seal(b"abc"))
        t[10] ^= 1
        with self.assertRaises(InvalidToken):
            vault.unseal(bytes(t))

    def test_wrong_key_cannot_decrypt(self):
        token = vault.seal(b"abc")
        with override_settings(FACE_AUTH_KEY=Fernet.generate_key().decode()):
            with self.assertRaises(InvalidToken):
                vault.unseal(token)

    def test_garbage_rejected(self):
        with self.assertRaises(InvalidToken):
            vault.unseal(b"not a token")
