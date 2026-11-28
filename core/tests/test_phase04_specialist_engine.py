"""Phase 04 — canonical Specialist Engine (real isolated stores and files)."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import opstatus, paths, specialist_engine as se


class SpecialistEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = self.base / "runs.json"
        self.flags = self.base / "flags.json"
        self.patches = [mock.patch.object(se, "_STORE", self.store),
                        mock.patch.object(se, "_FLAGS", self.flags)]
        for p in self.patches:
            p.start()
        self.project = "p4-unit"
        d = config.PROJECTS_DIR / self.project
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text('{"name":"p4-unit","type":"webapp"}',
                                     encoding="utf-8")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    # ------------------------------------------------------- discovery/registry
    def test_01_unique_registry_ids(self):
        reg = se.canonical_registry()
        self.assertGreaterEqual(len(reg), 20)
        self.assertEqual(len(reg), len({r["id"] for r in reg.values()}))
        for contract in reg.values():
            for key in ("inputs", "artifacts", "allowed_scopes", "depends_on",
                        "acceptance", "activation", "retry"):
                self.assertIn(key, contract)

    def test_02_role_contract_fields(self):
        role = se.get("developer")
        self.assertIn("project:write", role["allowed_scopes"])
        self.assertTrue(role["depends_on"])
        self.assertTrue(role["acceptance"])

    def test_03_legacy_aliases_resolve(self):
        self.assertEqual(se.resolve_id("builder"), "developer")
        self.assertEqual(se.resolve_id("qa"), "qa-engineer")
        self.assertEqual(se.get("builder")["id"], "developer")
        self.assertEqual(se.aliases()["builder"], "developer")

    def test_04_dynamic_selection(self):
        team = se.compose_team("webapp", "simple web page")
        ids = [t["role_id"] for t in team]
        self.assertIn("developer", ids)
        self.assertIn("qa-engineer", ids)
        self.assertNotIn("game-developer", ids)
        self.assertLessEqual(len(ids), len(se.canonical_registry()))

    def test_05_discovery_by_goal(self):
        found = {r["id"] for r in se.discover("", "بازی")}
        self.assertIn("game-developer", found)

    def test_06_registry_consistency(self):
        report = se.registry_consistency()
        self.assertTrue(report["ok"])
        self.assertEqual(report["problems"], [])

    # ------------------------------------------------------------- permissions
    def test_07_scope_granted_and_denied(self):
        self.assertTrue(se.require_scope("developer", "project:write"))
        with self.assertRaisesRegex(se.EngineError, "scope-denied"):
            se.require_scope("product-architect", "project:write")
        with self.assertRaises(se.EngineError):
            se.require_scope("unknown-role", "project:read")

    def test_08_prompt_cannot_grant_scope(self):
        # A contract with only read scope never gains write by instruction text.
        role = se.get("product-architect")
        self.assertNotIn("project:write", role["allowed_scopes"])
        with self.assertRaises(se.EngineError):
            se.require_scope("product-architect", "project:write")

    # ----------------------------------------------------------- handoff/hash
    def _write(self, rel, text):
        p = paths.project_path(self.project, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def test_09_full_text_handoff_with_hash(self):
        text = "# Target User\n.users\n## Scope\n.x\n## Success Criteria\ny\n"
        sha = self._write("spec/product-brief.md", text)
        result = se.verify_handoff(self.project, "software-architect",
                                   {"spec/product-brief.md": sha})
        self.assertTrue(result["verified"])
        item = result["items"][0]
        self.assertEqual(item["text"], text)
        self.assertEqual(item["sha256"], sha)
        self.assertEqual(item["length"], len(text))

    def test_10_handoff_tampering_detected(self):
        sha = self._write("spec/product-brief.md", "original")
        self._write("spec/product-brief.md", "tampered")
        result = se.verify_handoff(self.project, "software-architect",
                                   {"spec/product-brief.md": sha})
        self.assertFalse(result["verified"])
        self.assertTrue(any("hash-mismatch" in p for p in result["problems"]))

    def test_11_handoff_missing_artifact_fails_closed(self):
        artifact = paths.project_path(self.project, "spec/product-brief.md")
        if artifact.exists():
            artifact.unlink()
        result = se.verify_handoff(self.project, "software-architect")
        self.assertFalse(result["verified"])
        self.assertTrue(any("missing" in p for p in result["problems"]))

    # --------------------------------------------------------------- stale runs
    def test_12_stale_run_rejected_and_no_stale_pass(self):
        run = se.start_run(self.project, "developer", "tok-1")
        self.assertTrue(se.reject_stale(self.project, "developer", "tok-1")["accepted"])
        self.assertFalse(se.reject_stale(self.project, "developer", "wrong")["accepted"])
        # Simulate a stale lease: mark the run non-current, then attempt PASS.
        data = json.loads(self.store.read_text(encoding="utf-8"))
        data["runs"][run["key"]]["status"] = "REVISION"
        self.store.write_text(json.dumps(data), encoding="utf-8")
        result = se.record_result(self.project, "developer", "tok-1", opstatus.PASS)
        self.assertEqual(result["status"], opstatus.BLOCKED)
        self.assertIn("stale", result["error"])

    def test_13_current_run_pass_recorded(self):
        run = se.start_run(self.project, "qa-engineer", "tok-2")
        result = se.record_result(self.project, "qa-engineer", "tok-2",
                                  opstatus.PASS, artifacts=[])
        self.assertEqual(result["status"], opstatus.PASS)
        data = json.loads(self.store.read_text(encoding="utf-8"))
        self.assertEqual(data["runs"][run["key"]]["status"], opstatus.PASS)

    def test_14_dead_worker_stale_resolved(self):
        se.start_run(self.project, "developer", "tok-3")
        data = json.loads(self.store.read_text(encoding="utf-8"))
        key = "%s:developer:tok-3" % self.project
        data["runs"][key]["pid"] = 99999999
        self.store.write_text(json.dumps(data), encoding="utf-8")
        second = se.start_run(self.project, "developer", "tok-4")
        self.assertIn(key, second["stale_resolved"])
        data = json.loads(self.store.read_text(encoding="utf-8"))
        self.assertEqual(data["runs"][key]["status"], "REVISION")

    # -------------------------------------------------------- final response
    def test_15_final_response_skips_failed_and_uses_complete(self):
        messages = [
            {"type": "assistant", "finish": "stop", "completed": 3,
             "text": "newest complete"},
            {"type": "assistant", "finish": "aborted", "completed": 2},
            {"type": "assistant", "finish": "stop", "completed": 1},
        ]
        picked = se.select_final_response(messages)
        self.assertTrue(picked["ok"])
        self.assertEqual(picked["index"], 0)
        self.assertEqual(picked["message"]["text"], "newest complete")

    def test_16_no_complete_assistant_is_not_pass(self):
        picked = se.select_final_response([
            {"type": "assistant", "finish": "aborted", "completed": 1},
            {"type": "idle", "outcome": "failed"},
        ])
        self.assertFalse(picked["ok"])
        self.assertEqual(picked["reason"], "no-completed-assistant")

    # ------------------------------------------------------------ compat/flags
    def test_17_feature_flag_legacy_explicit_only(self):
        self.assertEqual(se.engine_mode(), "canonical")
        se.set_engine_mode("legacy")
        self.assertEqual(se.engine_mode(), "legacy")
        with self.assertRaises(se.EngineError):
            se.set_engine_mode("bad-mode")
        se.set_engine_mode("canonical")

    def test_18_corrupt_store_fails_closed(self):
        self.store.write_text("{broken", encoding="utf-8")
        with self.assertRaises(se.EngineError):
            se.start_run(self.project, "developer", "tok-x")


if __name__ == "__main__":
    unittest.main()
