"""AF NEXT P5 — Quality Gate severity, score independence and real checks."""
import json, unittest
from pathlib import Path

import core.config as config
from core import opstatus, quality_checks as qc, quality_gate as qg


def fixture(name="afp5-unit", ptype="webapp"):
    d = config.PROJECTS_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps({"name": name, "type": ptype}),
                                 encoding="utf-8")
    return d


class DecisionTest(unittest.TestCase):
    def test_critical_blocks_even_with_high_score(self):
        f = [qc.F("functionality", qg.CRITICAL, "boom", "x")]
        decision, why = qg.decide(f)
        self.assertEqual(decision, qg.BLOCK_RELEASE)
        self.assertTrue(any("boom" in w for w in why))

    def test_high_holds_unless_explicit_decision(self):
        f = [qc.F("visual", qg.HIGH, "screenshot-blocked", "x")]
        self.assertEqual(qg.decide(f)[0], qg.HOLD)
        d, why = qg.decide(f, {"decision_id": "EX-1", "actor": "user"})
        self.assertEqual(d, qg.RELEASE)
        self.assertTrue(any("EX-1" in w for w in why))

    def test_exception_never_clears_critical(self):
        f = [qc.F("security_privacy", qg.CRITICAL, "secret", "x"),
             qc.F("visual", qg.HIGH, "shot", "x")]
        self.assertEqual(qg.decide(f, {"decision_id": "EX-1", "actor": "u"})[0],
                         qg.BLOCK_RELEASE)

    def test_medium_low_release(self):
        f = [qc.F("ui_ux", qg.MEDIUM, "minor", "x"),
             qc.F("media", qg.LOW, "info", "x", status=qg.SKIPPED)]
        self.assertEqual(qg.decide(f)[0], qg.RELEASE)
        self.assertEqual(len(qg.DIMENSIONS), 10)

    def test_score_is_informational_only(self):
        dims = {"functionality": {"score": 100}, "visual": {"score": None}}
        self.assertEqual(qg.quality_score(dims), 100.0)
        f = [qc.F("functionality", qg.CRITICAL, "c", "x")]
        self.assertEqual(qg.decide(f)[0], qg.BLOCK_RELEASE)
        import inspect
        self.assertNotIn("score", inspect.signature(qg.decide).parameters)


class CheckTest(unittest.TestCase):
    def test_visual_detects_real_defects(self):
        files = {"index.html": '<html dir="ltr"><title>{{title}}</title>'
                               '<body>plain</body></html>'}
        f, cov = qc.analyze_visual(files, {"status": qg.BLOCKED,
                                           "error": "command-not-allowed"})
        codes = {x["code"] for x in f}
        self.assertIn("unfilled-placeholder", codes)
        self.assertIn("rtl-missing", codes)
        self.assertIn("screenshot-blocked", codes)
        self.assertIn("safe_path", json.dumps(f))
        self.assertEqual(cov["screenshot"], qg.BLOCKED)

    def test_ux_detects_missing_main_path_and_lorem(self):
        f = qc.analyze_ux({"index.html": "<html><title>x</title>"
                                        "<body><p>lorem ipsum</p></body></html>"})
        codes = {x["code"] for x in f}
        self.assertIn("no-main-path", codes)
        self.assertIn("untranslated-or-todo", codes)

    def test_media_and_performance_are_honest(self):
        self.assertEqual(qc.analyze_media({})[0]["status"], qg.SKIPPED)
        f = qc.analyze_performance("web_saas", 1200, {"tests.run": 40}, {"a": 1024})
        self.assertTrue(any(x["code"] == "build-measured" for x in f))
        self.assertTrue(any(x["code"] == "memory-cpu-not-measured" and
                            x["status"] == qg.NOT_VERIFIED for x in f))
        f2 = qc.analyze_performance("web_saas", 999999, {}, {})
        self.assertTrue(any(x["code"] == "build-slow" and
                            x["severity"] == qg.HIGH for x in f2))

    def test_security_critical_found(self):
        f = qc.analyze_security({"status": qg.PASS,
                                 "output": {"blocked": True,
                                            "critical": [{"kind": "secret"}]}})
        self.assertEqual(f[0]["severity"], qg.CRITICAL)


class EvaluateTest(unittest.TestCase):
    def test_governed_evaluation_writes_real_evidence(self):
        d = fixture("afp5-eval")
        (d / "index.html").write_text(
            '<!doctype html><html lang="fa" dir="rtl"><head>'
            '<meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width"><title>آزمون</title></head>'
            '<body><h1>آزمون</h1><button id="go">برو</button>'
            '<p id="err">خطا</p></body></html>', encoding="utf-8")
        rep = qg.evaluate("afp5-eval", "web_saas", do_build=False)
        self.assertEqual(rep["schema"], "afnext-p5-quality-1.0")
        self.assertIn(rep["decision"], (qg.RELEASE, qg.HOLD, qg.BLOCK_RELEASE))
        self.assertIsNotNone(rep["quality_score"])
        self.assertTrue(rep["evidence_files"]["report"]["sha256"])
        self.assertTrue((d / "quality/quality-report.json").is_file())
        self.assertTrue((d / "quality/release-evidence.json").is_file())
        summary = qg.gate(rep)
        self.assertEqual(summary["decision"], rep["decision"])
        self.assertTrue(rep["coverage"]["tool_calls"]["tests"])
        from core import orchestrator
        wrapped = orchestrator.quality_gate("afp5-eval", "web_saas", do_build=False)
        self.assertEqual(wrapped["decision"], rep["decision"])
        self.assertEqual(wrapped["engine"], "web_saas")


if __name__ == "__main__":
    unittest.main()
