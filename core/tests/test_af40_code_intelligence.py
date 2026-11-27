"""AF-40 — code intelligence, impact analysis and real post-change tests."""
import json
import unittest
from pathlib import Path

import core.config as config
from core import agent_planner, opstatus, project_snapshot


def _make(name: str) -> Path:
    d = config.PROJECTS_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    def w(rel, text):
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    w("meta.json", json.dumps({"name": name, "type": "webpage", "version": "1.0.0"}))
    w("app.py", "def compute(x):\n    return x + 1\n\n\ndef helper(y):\n    return compute(y)\n")
    w("server.py", "from app import compute\n\n\ndef main():\n    return compute(1)\n")
    w("tests/test_compute.py", "from app import compute\n\ndef test_compute():\n    assert compute(1) == 2\n\ntest_compute()\n")
    w("api-contract.json", json.dumps({"endpoints": {"register": "/api/register"},
                                       "status": {"create": 201}}))
    w("README.md", "# AF40 demo\n")
    return d


class CodeIntelligenceTest(unittest.TestCase):
    def test_ast_symbols_and_line_tagged_references(self):
        name = "af40-code"
        _make(name)
        info = project_snapshot.analyze_code(name)
        self.assertEqual(info["status"], "verified")
        syms = [s for s in info["symbols"] if s["name"] == "compute"]
        self.assertTrue(any(s["file"] == "app.py" and s["line"] == 1 for s in syms))
        refs = [r for r in info["refs"] if r["kind"] == "call" and
                r["to"] == "app.py"]
        self.assertTrue(refs, info["refs"])
        self.assertTrue(all("line" in r and "source" in r for r in refs))
        self.assertTrue(any(p["status"] == "verified" for p in info["paths"]))

    def test_line_numbered_context_is_relevant_and_bounded(self):
        name = "af40-context"
        _make(name)
        text = project_snapshot.select_context(name, "fix compute in app.py",
                                               max_chars=1800)
        self.assertIn("[app.py:", text)
        self.assertIn("compute", text)
        self.assertIn("### پایان کد مرتبط", text)
        self.assertLessEqual(len(text), 2000)
        self.assertIn("status=verified", text)

    def test_impact_includes_direct_indirect_contracts_tests(self):
        name = "af40-impact"
        _make(name)
        imp = project_snapshot.impact(name, ["app.py"], goal="change compute")
        self.assertEqual((imp.get("targets") or {}).get("status"), "verified")
        direct = (imp.get("direct") or {}).get("value") or []
        self.assertTrue(any(r["from"] in ("server.py", "tests/test_compute.py")
                            for r in direct), direct)
        self.assertEqual((imp.get("contracts") or {}).get("status"), "verified")
        self.assertTrue((imp.get("tests") or {}).get("value"))
        self.assertIn((imp.get("risk") or {}).get("value"), ("low", "medium", "high"))

    def test_reanalyze_after_real_change_invalidates_stale_facts(self):
        name = "af40-refresh"
        d = _make(name)
        snap = project_snapshot.build(name)
        self.assertEqual(snap["status"], "ok")
        plan = agent_planner.create(name, "change compute", snapshot=snap)
        self.assertIn("project_model", plan)
        # Change a DEPENDENCY function the callers rely on.
        (d / "app.py").write_text("def compute(x):\n    return x - 1\n\n\ndef helper(y):\n    return compute(y)\n",
                                  encoding="utf-8")
        re = project_snapshot.reanalyze(name, ["app.py"])
        self.assertEqual(re["status"], "ok")
        self.assertIn("app.py", re["changed"])
        stored = project_snapshot.stored(name)
        self.assertIn("app.py", (stored or {}).get("signatures", {}))

    def test_real_test_command_runs_and_detects_regression(self):
        name = "af40-regression"
        d = _make(name)
        cmd = project_snapshot.related_test_command(name, ["app.py"])
        self.assertEqual(cmd["status"], "verified")
        self.assertTrue(cmd["command"])
        first = project_snapshot.run_related_tests(name, ["app.py"])
        self.assertTrue(first["ran"], first)
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["status"], opstatus.PASS)
        # Break the real dependency: callers now fail.
        (d / "app.py").write_text("def compute(x):\n    return x - 100  # real regression\n\n\ndef helper(y):\n    return compute(y)\n",
                                  encoding="utf-8")
        broken = project_snapshot.run_related_tests(name, ["app.py"])
        self.assertFalse(broken["ok"], broken)
        self.assertEqual(broken["status"], opstatus.FAIL)

    def test_unknown_language_is_honest(self):
        name = "af40-unknown"
        d = config.PROJECTS_DIR / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps({"name": name, "type": "webpage"}))
        (d / "mystery.afx").write_text("whatever", encoding="utf-8")
        info = project_snapshot.analyze_code(name)
        self.assertEqual(info["status"], "unknown")
        self.assertIn(".afx", info["unknown_languages"])


    def test_baseline_survives_build_and_reports_real_diff(self):
        name = "af41-baseline"
        d = _make(name)
        project_snapshot.set_baseline(name)
        # build()/analysis must not rewrite the pre-change baseline.
        project_snapshot.build(name)
        project_snapshot.analyze_code(name)
        before = (project_snapshot.stored(name) or {}).get("baseline") or {}
        self.assertIn("app.py", before)
        (d / "app.py").write_text("def compute(x):\n    return x + 777\n",
                                  encoding="utf-8")
        project_snapshot.build(name)     # would have erased baseline before AF-41
        diff = project_snapshot.changed_files(name)
        self.assertIn("app.py", diff["modified"], diff)
        self.assertNotIn("app.py", diff["added"])


if __name__ == "__main__":
    unittest.main()
