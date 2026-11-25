import os
import shutil
import stat
import tempfile
import unittest
from unittest import mock

from django.core.exceptions import SuspiciousFileOperation
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, override_settings

from accounts.storage import PrivateStorage


@unittest.skipUnless(os.name == "posix", "needs POSIX")
class PrivateStorageTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        ctx = override_settings(PRIVATE_MEDIA_ROOT=self.tmp)
        ctx.enable()
        self.addCleanup(ctx.disable)
        self.s = PrivateStorage()

    def test_saves_even_when_the_filesystem_cannot_lock(self):
        data = b"x" * 3_000_000
        with mock.patch("fcntl.flock", side_effect=OSError(38, "Function not implemented")):
            name = self.s.save("kyc/a.jpg", ContentFile(data))
        with open(self.s.path(name), "rb") as f:
            self.assertEqual(f.read(), data)

    def test_files_are_owner_only(self):
        name = self.s.save("kyc/a.jpg", ContentFile(b"x"))
        self.assertEqual(stat.S_IMODE(os.stat(self.s.path(name)).st_mode), 0o600)

    def test_a_second_file_with_the_same_name_never_overwrites_the_first(self):
        a = self.s.save("kyc/a.jpg", ContentFile(b"first"))
        b = self.s.save("kyc/a.jpg", ContentFile(b"second"))
        self.assertNotEqual(a, b)
        with open(self.s.path(a), "rb") as f:
            self.assertEqual(f.read(), b"first")

    def test_a_failed_write_leaves_no_partial_file(self):
        class Broken(ContentFile):
            def chunks(self, chunk_size=None):
                yield b"abc"
                raise OSError("disk full")
        with self.assertRaises(OSError):
            self.s.save("kyc/a.jpg", Broken(b""))
        self.assertFalse(os.path.exists(self.s.path("kyc/a.jpg")))

    def test_names_cannot_escape_the_folder(self):
        with self.assertRaises(SuspiciousFileOperation):
            self.s.save("../evil.jpg", ContentFile(b"x"))