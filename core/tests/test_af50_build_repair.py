"""AF-50 — structured Android compiler findings (UNIT, not E2E)."""
import unittest
from pathlib import Path

from core.engines.android import _compile_findings
from core.engines.registry import EngineError


class CompileFindingsTest(unittest.TestCase):
    def test_real_javac_output_is_structured(self):
        out = ("java/com/aifactory/test/WebViewBridge.java:36: error: "
               "incompatible types: JSONObject cannot be converted to String\n"
               "        return repository.add(title, description, category, done);\n"
               "                             ^\n"
               "Note: Some messages have been simplified\n")
        d = Path('/data/data/com.termux/files/home/AI-Factory/projects/af49-todo')
        f = _compile_findings(out, d)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["file"], "java/com/aifactory/test/WebViewBridge.java")
        self.assertEqual(f[0]["line"], 36)
        self.assertEqual(f[0]["kind"], "error")
        self.assertIn("JSONObject cannot be converted to String", f[0]["message"])

    def test_engine_error_carries_findings(self):
        e = EngineError(500, "build failed", findings=[{"file": "x.java"}])
        self.assertEqual(e.findings[0]["file"], "x.java")

    def test_no_findings_returns_empty(self):
        self.assertEqual(_compile_findings("random failure"), [])


if __name__ == "__main__":
    unittest.main()
