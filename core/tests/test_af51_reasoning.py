"""AF-51 — reasoning level → real model variant (UNIT, no network)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import reasoning


class ReasoningTest(unittest.TestCase):
    def test_real_variants_map_exactly(self):
        model = {"providerID": "opencode", "modelID": "beam-free",
                 "variants": [{"id": v, "settings": {"reasoningEffort": v}}
                              for v in ("low", "medium", "high", "max")]}
        for level, want in (("low", "low"), ("medium", "medium"),
                            ("high", "high"), ("maximum", "max")):
            r = reasoning.resolve(level, model)
            self.assertTrue(r["supported"], r)
            self.assertEqual(r["variant"], want)
            self.assertTrue(r["exact"])

    def test_unsupported_model_is_honest(self):
        r = reasoning.resolve("high", {"providerID": "opencode",
                                       "modelID": "no-effort", "variants": []})
        self.assertFalse(r["supported"])
        self.assertIsNone(r["variant"])
        self.assertIn("هیچ سطح تفکری", r["note"])

    def test_inexact_level_is_flagged(self):
        model = {"variants": [{"id": "low"}, {"id": "high"}]}
        r = reasoning.resolve("medium", model)
        self.assertTrue(r["supported"])
        self.assertFalse(r["exact"])
        self.assertIn("نزدیکترین", r["note"])

    def test_auto_uses_real_capability_and_complexity(self):
        model = {"variants": [{"id": "low"}, {"id": "medium"}, {"id": "high"}]}
        self.assertEqual(reasoning.resolve("auto", model, "hello")["variant"], "medium")
        r = reasoning.resolve("auto", model, "ساخت معماری پیچیده", "pipeline")
        self.assertEqual(r["variant"], "high")
        self.assertTrue(r["exact"])

    def test_default_persists(self):
        with tempfile.NamedTemporaryFile(suffix=".json") as tf:
            with mock.patch.object(reasoning, "_STORE", Path(tf.name)):
                reasoning.set_default_level("high")
                self.assertEqual(reasoning.default_level(), "high")
                with self.assertRaises(ValueError):
                    reasoning.set_default_level("bogus")


if __name__ == "__main__":
    unittest.main()
