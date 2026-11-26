import importlib.util
from unittest import mock, skipUnless

from django.test import TestCase, override_settings

from .. import engine
from .helpers import vec


class EngineTests(TestCase):
    def test_similarity_identical_orthogonal_opposite_zero(self):
        a, b, c = vec(1), vec(0, 1), vec(-1)
        self.assertAlmostEqual(engine.similarity(a, a), 1.0, places=5)
        self.assertAlmostEqual(engine.similarity(a, b), 0.0, places=5)
        self.assertAlmostEqual(engine.similarity(a, c), -1.0, places=5)
        self.assertEqual(engine.similarity(vec(), a), 0.0)          # zero vector must not divide by zero

    def test_similarity_ignores_scale(self):
        self.assertAlmostEqual(engine.similarity(vec(1, 1), vec(5, 5)), 1.0, places=5)

    def test_rules_cover_every_label(self):
        from ..models import LABELS
        self.assertEqual(set(engine.RULES), set(LABELS))

    def test_rules_thresholds(self):
        base = dict(smile=0, blink=0, open=0, yaw=0, pitch=0.5)
        r = engine.RULES
        self.assertTrue(r["smile"]({**base, "smile": 0.9}))
        self.assertFalse(r["smile"]({**base, "smile": 0.1}))
        self.assertTrue(r["blink"]({**base, "blink": 0.9}))
        self.assertFalse(r["blink"](base))
        self.assertTrue(r["open"]({**base, "open": 0.9}))
        self.assertFalse(r["open"](base))
        self.assertTrue(r["up"]({**base, "pitch": 0.2}))
        self.assertFalse(r["up"](base))

    def test_turn_rules_are_mutually_exclusive(self):
        base = dict(smile=0, blink=0, open=0, pitch=0.5)
        left, right = {**base, "yaw": 0.3}, {**base, "yaw": -0.3}
        self.assertNotEqual(engine.RULES["left"](left), engine.RULES["right"](left))
        self.assertNotEqual(engine.RULES["left"](right), engine.RULES["right"](right))
        neutral = {**base, "yaw": 0.0}
        self.assertFalse(engine.RULES["left"](neutral))             # facing front is neither
        self.assertFalse(engine.RULES["right"](neutral))

    @override_settings(FACE_MODEL_DIR="/nonexistent")
    def test_available_false_when_models_missing(self):
        self.assertFalse(engine.available())

    @skipUnless(importlib.util.find_spec("cv2"), "needs OpenCV")
    def test_analyse_rejects_unreadable_and_mismatched(self):
        with mock.patch.object(engine, "_load", return_value={}):
            self.assertEqual(engine.analyse([b"x"], ["smile", "left"])[2], "unreadable")   # length mismatch
            self.assertEqual(engine.analyse([b"not an image"], ["smile"])[2], "unreadable")
