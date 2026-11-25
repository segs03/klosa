import sys
from unittest import mock

from django.test import SimpleTestCase

from accounts.face_local import LocalFace


class LocalFaceFallbackTests(SimpleTestCase):
    def test_without_opencv_a_person_reviews_it(self):
        with mock.patch.dict(sys.modules, {"cv2": None}):
            self.assertEqual(LocalFace().assess(mock.Mock())[0], "review")

    def test_any_error_falls_back_to_review(self):
        with mock.patch.object(LocalFace, "_assess", side_effect=RuntimeError("boom")):
            self.assertEqual(LocalFace().assess(mock.Mock())[0], "review")