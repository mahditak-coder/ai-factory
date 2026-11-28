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

    # ------------------------------------------- Finding A/B audit regressions
    def test_18_topological_order_education_curriculum(self):
        r = self._team("android", "اپلیکیشن آموزشی اندروید",
                       capabilities=["curriculum"])
        self.assertTrue(r["ok"], r["problems"])
        ids = self._ids(r)
        self.assertIn("education-specialist", ids)
        self.assertIn("curriculum-designer", ids)
        self.assertLess(ids.index("education-specialist"),
                        ids.index("curriculum-designer"))

    def test_19_every_dependency_precedes_consumer(self):
        for ptype, goal, kw in (
            ("webpage", "ساخت صفحه وب ساده", {}),
            ("game", "ساخت بازی اندرویدی", {}),
            ("android", "اپلیکیشن آموزشی اندروید",
             {"capabilities": ["curriculum"]}),
        ):
            with self.subTest(goal=goal):
                r = self._team(ptype, goal, **kw)
                self.assertTrue(r["ok"], r["problems"])
                ids = self._ids(r)
                for item in r["team"]:
                    for dep in item["dependencies"]:
                        self.assertIn(dep, ids)
                        self.assertLess(ids.index(dep),
                                        ids.index(item["role_id"]))

    def test_20_unauthorized_scopes_rejected(self):
        contract = se.get("product-architect")
        base = {"role_id": "product-architect",
                "stage_id": "role:product-architect",
                "title": contract["title"], "domain": "software",
                "dependencies": [se.resolve_id(d)
                                 for d in contract["depends_on"]],
                "inputs": list(contract["inputs"]),
                "expected_artifacts": list(contract["artifacts"]),
                "allowed_scopes": list(contract["allowed_scopes"]),
                "acceptance": list(contract["acceptance"])}
        for bad in ("network:admin", "project:delete", "credentials:read"):
            with self.subTest(scope=bad):
                item = dict(base)
                item["allowed_scopes"] = list(base["allowed_scopes"]) + [bad]
                report = dt.validate_team([item])
                self.assertFalse(report["ok"])
                self.assertTrue(any("unauthorized-scope" in p
                                    for p in report["problems"]))
        self.assertTrue(dt.validate_team([base])["ok"])

    def test_21_forged_artifact_and_dependency_rejected(self):
        r = self._team("webpage", "ساخت صفحه وب ساده")
        item = dict(r["team"][0])
        forged = dict(item)
        forged["expected_artifacts"] = [{"path": "spec/fake.md",
                                         "format": "md",
                                         "required_sections": []}]
        self.assertFalse(dt.validate_team([forged])["ok"])
        fake_dep = dict(item)
        fake_dep["dependencies"] = ["fake-role"]
        report = dt.validate_team([fake_dep])
        self.assertFalse(report["ok"])
        self.assertTrue(any("dependency-mismatch" in p
                            for p in report["problems"]))
        game = self._team("game", "ساخت بازی اندرویدی")
        with_dep = next(m for m in game["team"] if m["dependencies"])
        removed = dict(with_dep)
        removed["dependencies"] = []
        self.assertFalse(dt.validate_team([removed])["ok"])

    def test_22_alias_resolves_in_validation(self):
        self.assertEqual(se.resolve_id("backend"), "backend-specialist")
        self.assertEqual(se.resolve_id("product-director"), "product-architect")
        contract = se.get("product-director")
        item = {"role_id": "product-director",
                "inputs": list(contract["inputs"]),
                "expected_artifacts": list(contract["artifacts"]),
                "dependencies": [se.resolve_id(d)
                                 for d in contract["depends_on"]],
                "allowed_scopes": list(contract["allowed_scopes"]),
                "acceptance": list(contract["acceptance"])}
        self.assertTrue(dt.validate_team([item])["ok"])



if __name__ == "__main__":
    unittest.main()
