"""Phase 05 — deterministic domain team activation tests (no execution)."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
import core.domain_teams as dt
import core.specialist_engine as se
import core.workflow_roles as wr


class DomainTeamsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.flags = Path(self.tmp.name) / "flags.json"
        self.patcher = mock.patch.object(dt, "_FLAGS", self.flags)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def _team(self, ptype="", goal="", **kw):
        return dt.build_team(ptype, goal, **kw)

    def _ids(self, result):
        return [item["role_id"] for item in result["team"]]

    # ------------------------------------------------------ required cases
    def test_01_simple_web_only_web_roles(self):
        r = self._team("webpage", "ساخت صفحه وب ساده")
        self.assertTrue(r["ok"], r["problems"])
        ids = set(self._ids(r))
        self.assertIn("developer", ids)
        self.assertIn("ui-ux-designer", ids)
        self.assertFalse(ids & {
            "game-developer", "audio-game", "music-sound-specialist",
            "character-artist", "environment-artist", "technical-artist-25d",
            "level-designer", "game-balance-specialist"})

    def test_02_game_android_activates_game_and_android(self):
        r = self._team("game", "ساخت بازی اندرویدی")
        self.assertTrue(r["ok"], r["problems"])
        self.assertEqual(r["context"]["domain"], "game")
        self.assertEqual(r["context"]["platform"], "Android")
        ids = set(self._ids(r))
        self.assertIn("game-designer", ids)
        self.assertIn("game-developer", ids)
        self.assertIn("android-developer", ids)

    def test_03_game_web_no_android_assumption(self):
        r = self._team("game", "ساخت بازی وب")
        self.assertTrue(r["ok"], r["problems"])
        self.assertEqual(r["context"]["platform"], "Web")
        self.assertNotIn("android-developer", self._ids(r))
        self.assertIn("game-developer", self._ids(r))

    def test_04_education_android_without_game_roles(self):
        r = self._team("android", "اپلیکیشن آموزشی اندروید")
        self.assertTrue(r["ok"], r["problems"])
        self.assertEqual(r["context"]["domain"], "education")
        self.assertEqual(r["context"]["platform"], "Android")
        ids = set(self._ids(r))
        self.assertIn("education-specialist", ids)
        self.assertIn("android-developer", ids)
        self.assertFalse(ids & {"game-developer", "level-designer",
                                "game-balance-specialist"})

    def test_05_software_android_not_game(self):
        r = self._team("android", "اپلیکیشن نرم افزار اندروید")
        self.assertTrue(r["ok"], r["problems"])
        self.assertEqual(r["context"]["domain"], "software")
        self.assertIn("android-developer", self._ids(r))
        self.assertNotIn("game-developer", self._ids(r))

    def test_06_ambiguous_request_fails_closed(self):
        r = self._team("", "بازی آموزشی بساز")
        self.assertFalse(r["ok"])
        self.assertEqual(r["context"]["domain"], "ambiguous")
        self.assertTrue(any("essential-clarification" in p
                            for p in r["problems"]))
        self.assertEqual(r["team"], [])

    def test_07_unnecessary_role_not_activated(self):
        r = self._team("webpage", "ساخت صفحه وب ساده")
        self.assertNotIn("devops-specialist", self._ids(r))
        self.assertNotIn("api-specialist", self._ids(r))
        self.assertNotIn("database-specialist", self._ids(r))

    def test_08_unknown_role_fails_closed(self):
        registry = se.canonical_registry()
        registry.pop("developer", None)
        with mock.patch.object(se, "canonical_registry", return_value=registry):
            r = self._team("webpage", "ساخت صفحه وب ساده")
        self.assertFalse(r["ok"])
        self.assertTrue(any("role" in p or "dependency" in p
                            for p in r["problems"]))

    def test_09_dependency_cycle_rejected(self):
        registry = se.canonical_registry()
        a = dict(registry["ui-ux-designer"])
        a["depends_on"] = ("developer",)
        b = dict(registry["developer"])
        b["depends_on"] = ("ui-ux-designer",)
        registry["ui-ux-designer"] = a
        registry["developer"] = b
        with mock.patch.object(se, "canonical_registry", return_value=registry):
            r = self._team("webpage", "ساخت صفحه وب ساده")
        self.assertFalse(r["ok"])
        self.assertTrue(any("dependency-cycle" in p for p in r["problems"]))

    def test_10_incomplete_artifact_rejected(self):
        team = [{"role_id": "developer", "expected_artifacts": [],
                 "dependencies": [], "allowed_scopes": ["project:write"]}]
        report = dt.validate_team(team)
        self.assertFalse(report["ok"])
        self.assertTrue(any("missing-artifacts" in p for p in report["problems"]))

    def test_11_forged_scope_rejected(self):
        team = [{"role_id": "backend-specialist",
                 "expected_artifacts": [{"path": "spec/backend-design.md"}],
                 "dependencies": [],
                 "allowed_scopes": ["project:write", "shell:execute"]}]
        report = dt.validate_team(team)
        self.assertFalse(report["ok"])
        self.assertTrue(any("unauthorized-scope" in p
                            for p in report["problems"]))
        with self.assertRaises(se.EngineError):
            se.require_scope("backend-specialist", "shell:execute")

    def test_12_legacy_alias_resolves(self):
        self.assertEqual(se.resolve_id("backend"), "backend-specialist")
        self.assertEqual(se.get("qa")["id"], "qa-engineer")
        report = se.catalog_reconciliation()
        self.assertTrue(report["ok"], report["problems"])

    def test_13_feature_flag_off_keeps_phase04_path(self):
        dt.set_domain_flag("game", False)
        try:
            r = self._team("game", "ساخت بازی اندرویدی")
            self.assertTrue(r["ok"])
            self.assertEqual(r.get("mode"), "phase04")
            self.assertTrue(all(m["role_id"] in se.canonical_registry()
                                for m in r["team"]))
        finally:
            dt.set_domain_flag("game", True)

    def test_14_concurrent_team_builds_identical(self):
        out, lock = [], threading.Lock()
        barrier = threading.Barrier(3)

        def work():
            barrier.wait(timeout=10)
            r = self._team("webpage", "ساخت صفحه وب ساده")
            with lock:
                out.append(r)

        threads = [threading.Thread(target=work) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        self.assertEqual(len(out), 3)
        self.assertEqual(len({r["hash"] for r in out}), 1)
        for r in out:
            ids = self._ids(r)
            self.assertEqual(len(ids), len(set(ids)))

    # ------------------------------------------------------------- extras
    def test_15_capability_activation(self):
        r = self._team("webapp", "ساخت وب اپلیکیشن با backend و database")
        self.assertTrue(r["ok"], r["problems"])
        ids = set(self._ids(r))
        self.assertIn("backend-specialist", ids)
        self.assertIn("database-specialist", ids)

    def test_16_domain_platform_independence(self):
        android_game = self._team("game", "ساخت بازی اندرویدی")
        web_game = self._team("game", "ساخت بازی وب")
        self.assertEqual(android_game["context"]["domain"],
                         web_game["context"]["domain"])
        self.assertNotEqual(android_game["context"]["platform"],
                            web_game["context"]["platform"])
        self.assertIn("android-developer", self._ids(android_game))
        self.assertNotIn("android-developer", self._ids(web_game))

    def test_17_contract_fields_complete(self):
        registry = se.canonical_registry()
        for rec in registry.values():
            for key in ("inputs", "artifacts", "allowed_scopes", "depends_on",
                        "acceptance", "retry", "evidence_required"):
                self.assertIn(key, rec)


if __name__ == "__main__":
    unittest.main()
