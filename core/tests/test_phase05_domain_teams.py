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


    # --------------------------------- Finding: exact scope-list validation
    def _contract_item(self, role_id):
        contract = se.get(role_id)
        return {"role_id": role_id,
                "inputs": list(contract["inputs"]),
                "expected_artifacts": list(contract["artifacts"]),
                "dependencies": [se.resolve_id(d)
                                 for d in contract["depends_on"]],
                "allowed_scopes": list(contract["allowed_scopes"]),
                "acceptance": list(contract["acceptance"])}

    def test_23_exact_canonical_scope_list_passes(self):
        item = self._contract_item("product-architect")
        self.assertEqual(item["allowed_scopes"],
                         se.get("product-architect")["allowed_scopes"])
        self.assertTrue(dt.validate_team([item])["ok"])

    def test_24_removing_one_valid_scope_fails(self):
        item = self._contract_item("developer")
        item["allowed_scopes"] = item["allowed_scopes"][:-1]
        report = dt.validate_team([item])
        self.assertFalse(report["ok"])
        self.assertTrue(any("omitted-scope" in p for p in report["problems"]))

    def test_25_removing_all_scopes_fails(self):
        item = self._contract_item("product-architect")
        item["allowed_scopes"] = []
        report = dt.validate_team([item])
        self.assertFalse(report["ok"])
        self.assertTrue(any("missing-scopes" in p for p in report["problems"]))

    def test_26_unauthorized_scope_fails(self):
        item = self._contract_item("product-architect")
        item["allowed_scopes"] = item["allowed_scopes"] + ["network:admin"]
        report = dt.validate_team([item])
        self.assertFalse(report["ok"])
        self.assertTrue(any("unauthorized-scope" in p
                            for p in report["problems"]))

    def test_27_duplicate_scope_fails(self):
        item = self._contract_item("product-architect")
        item["allowed_scopes"] = item["allowed_scopes"] + \
            [item["allowed_scopes"][0]]
        report = dt.validate_team([item])
        self.assertFalse(report["ok"])
        self.assertTrue(any("duplicate-scope" in p
                            for p in report["problems"]))

    def test_28_reordered_scopes_fail(self):
        item = self._contract_item("developer")
        self.assertGreater(len(item["allowed_scopes"]), 1)
        item["allowed_scopes"] = list(reversed(item["allowed_scopes"]))
        report = dt.validate_team([item])
        self.assertFalse(report["ok"])
        self.assertTrue(any("scope-order-mismatch" in p
                            for p in report["problems"]))

    def test_29_malformed_scopes_fail(self):
        item = self._contract_item("product-architect")
        item["allowed_scopes"] = "project:read"
        report = dt.validate_team([item])
        self.assertFalse(report["ok"])
        self.assertTrue(any("malformed-scopes" in p
                            for p in report["problems"]))
        item["allowed_scopes"] = None
        self.assertFalse(dt.validate_team([item])["ok"])


    # -------------------------------- Finding: malformed scope input safety
    def _malformed_scope_check(self, bad_value):
        item = self._contract_item("product-architect")
        item["allowed_scopes"] = [bad_value]
        # Must not raise TypeError/ValueError for unhashable inputs.
        report = dt.validate_team([item])
        self.assertFalse(report["ok"])
        self.assertTrue(any("malformed-scope" in p
                            for p in report["problems"]))
        return report

    def test_30_nested_list_scope_element(self):
        report = self._malformed_scope_check(["project:read"])
        self.assertIn("malformed-scope", " ".join(report["problems"]))

    def test_31_dict_scope_element(self):
        self._malformed_scope_check({"scope": "project:read"})

    def test_32_integer_scope_element(self):
        self._malformed_scope_check(42)

    def test_33_boolean_scope_element(self):
        self._malformed_scope_check(True)
        self._malformed_scope_check(False)

    def test_34_null_scope_element(self):
        self._malformed_scope_check(None)

    def test_35_mixed_valid_and_invalid_scope_elements(self):
        item = self._contract_item("product-architect")
        item["allowed_scopes"] = ["project:read", ["nested"]]
        report = dt.validate_team([item])
        self.assertFalse(report["ok"])
        self.assertTrue(any("malformed-scope:product-architect:1" in p
                            for p in report["problems"]))

    def test_36_valid_scope_list_still_exact(self):
        item = self._contract_item("product-architect")
        self.assertTrue(dt.validate_team([item])["ok"])
        item["allowed_scopes"] = list(reversed(item["allowed_scopes"])) \
            if len(item["allowed_scopes"]) > 1 else ["project:write"]
        self.assertFalse(dt.validate_team([item])["ok"])



    # ================= GATE 2: consolidated audit regressions (findings A-H)
    # -------------------------------- Finding A: custom str subclasses
    def test_37_hashable_str_subclass_scope_rejected(self):
        class HashableStr(str):
            pass
        item = self._contract_item("product-architect")
        item["allowed_scopes"] = [HashableStr("project:read")]
        report = dt.validate_team([item])
        self.assertFalse(report["ok"])
        self.assertTrue(any("malformed-scope" in p
                            for p in report["problems"]))

    def test_38_unhashable_str_subclass_scope_rejected(self):
        class UnhashableStr(str):
            __hash__ = None
        item = self._contract_item("product-architect")
        item["allowed_scopes"] = [UnhashableStr("project:read")]
        report = dt.validate_team([item])
        self.assertFalse(report["ok"])
        self.assertTrue(any("malformed-scope" in p
                            for p in report["problems"]))

    # -------------------------------- Finding B: aliases + collisions
    def test_39_alias_policy_and_backward_compatibility(self):
        self.assertEqual(se.resolve_id("frontend"), "ui-ux-designer")
        self.assertEqual(se.resolve_id("front-end"), "frontend-specialist")
        self.assertEqual(se.resolve_id("frontend-specialist"),
                         "frontend-specialist")
        rep = se.alias_report()
        self.assertTrue(rep["ok"], rep["problems"])

    def test_40_aliases_have_no_cycle_shadow_or_unresolved_target(self):
        rep = se.alias_report()
        bad = [p for p in rep["problems"]
               if p.startswith(("alias-cycle", "alias-unresolved",
                                "alias-shadows-canonical"))]
        self.assertEqual(bad, [], rep["problems"])
        self.assertTrue(se.catalog_reconciliation()["ok"])

    # -------------------------------- Finding C: eligibility / product type
    def test_41_capability_cannot_activate_game_only_role(self):
        r = self._team("webpage", "ساخت صفحه وب",
                       capabilities=["level", "balance"])
        self.assertTrue(r["ok"], r["problems"])
        ids = set(self._ids(r))
        for role in ("level-designer", "game-balance-specialist",
                     "game-developer", "game-designer"):
            self.assertNotIn(role, ids)

    def test_42_cross_domain_creative_in_game_request(self):
        r = self._team("game", "ساخت بازی با داستان و موسیقی",
                       capabilities=["narrative", "music"])
        self.assertTrue(r["ok"], r["problems"])
        ids = set(self._ids(r))
        self.assertIn("game-designer", ids)
        self.assertIn("narrative-designer", ids)
        self.assertIn("music-sound-specialist", ids)

    def test_43_education_role_not_activated_on_software_request(self):
        r = self._team("webpage", "ساخت صفحه وب",
                       capabilities=["curriculum", "learning"])
        self.assertTrue(r["ok"], r["problems"])
        ids = set(self._ids(r))
        for role in ("education-specialist", "curriculum-designer",
                     "learning-experience-specialist"):
            self.assertNotIn(role, ids)

    def test_44_domain_platform_combinations_are_valid(self):
        cases = (
            ("webpage", "ساخت صفحه وب ساده", "software", "Web"),
            ("android", "اپلیکیشن نرم افزار اندروید", "software", "Android"),
            ("game", "ساخت بازی اندرویدی", "game", "Android"),
            ("game", "ساخت بازی وب", "game", "Web"),
            ("android", "اپلیکیشن آموزشی اندروید", "education", "Android"),
            ("webapp", "وب اپلیکیشن با backend", "software", "Web"),
            ("webpage", "طراحی رابط کاربری", "design", "Web"),
            ("webpage", "ساخت فایل صدا و موسیقی", "media", "Web"),
        )
        for ptype, goal, domain, platform in cases:
            with self.subTest(goal=goal):
                r = self._team(ptype, goal)
                self.assertTrue(r["ok"], r["problems"])
                self.assertEqual(r["context"]["domain"], domain)
                self.assertEqual(r["context"]["platform"], platform)
                self.assertTrue(self._ids(r))

    def test_45_ambiguous_and_unknown_fail_closed(self):
        for goal in ("بازی آموزشی بساز", ""):
            with self.subTest(goal=goal):
                r = self._team("", goal)
                self.assertFalse(r["ok"])
                self.assertEqual(r["team"], [])

    # -------------------------------- Finding D: every domain flag
    def test_46_every_domain_flag_is_effective(self):
        for dom in dt.DOMAINS:
            with self.subTest(dom=dom):
                self.assertTrue(dt.domain_enabled(dom))
                dt.set_domain_flag(dom, False)
                try:
                    self.assertFalse(dt.domain_enabled(dom))
                finally:
                    dt.set_domain_flag(dom, True)
                self.assertTrue(dt.domain_enabled(dom))

    def test_47_android_flag_gates_android_roles_in_all_domains(self):
        dt.set_domain_flag("android", False)
        try:
            for ptype, goal in (
                ("android", "اپلیکیشن نرم افزار اندروید"),
                ("android", "اپلیکیشن آموزشی اندروید"),
                ("game", "ساخت بازی اندرویدی"),
            ):
                with self.subTest(goal=goal):
                    r = self._team(ptype, goal)
                    self.assertTrue(r["ok"], r["problems"])
                    ids = set(self._ids(r))
                    self.assertNotIn("android-developer", ids)
                    self.assertNotIn("mobile-ux-specialist", ids)
        finally:
            dt.set_domain_flag("android", True)

    def test_48_media_flag_blocks_cross_domain_roles(self):
        dt.set_domain_flag("media", False)
        try:
            r = self._team("game", "ساخت بازی با داستان",
                           capabilities=["narrative", "music"])
            self.assertTrue(r["ok"], r["problems"])
            ids = set(self._ids(r))
            self.assertNotIn("narrative-designer", ids)
            self.assertNotIn("music-sound-specialist", ids)
        finally:
            dt.set_domain_flag("media", True)

    def test_49_disabled_secondary_domain_no_dependency_leak(self):
        dt.set_domain_flag("software", False)
        try:
            r = self._team("game", "ساخت بازی اندرویدی")
            ids = set(self._ids(r))
            self.assertFalse(any(dt._role_domain(rid) == "software"
                                 for rid in ids))
        finally:
            dt.set_domain_flag("software", True)

    # -------------------------------- Finding E: flag storage
    def test_50_missing_flag_file_is_first_run_default(self):
        self.assertFalse(self.flags.exists())
        self.assertTrue(dt.domain_enabled("game"))
        self.assertFalse(self.flags.exists())

    def test_51_corrupt_json_fails_closed_without_overwrite(self):
        self.flags.write_text("{not json", encoding="utf-8")
        for dom in dt.DOMAINS:
            self.assertFalse(dt.domain_enabled(dom))
        with self.assertRaises(dt.FlagStoreError):
            dt.set_domain_flag("game", False)
        self.assertEqual(self.flags.read_text(encoding="utf-8"), "{not json")

    def test_52_invalid_root_and_flag_types_fail_closed(self):
        for payload in ('[]', 'null', '{"domain:game": "false"}',
                        '{"domain:game": 1}', '{"bad": true}'):
            with self.subTest(payload=payload):
                self.flags.write_text(payload, encoding="utf-8")
                self.assertFalse(dt.domain_enabled("game"))
                self.assertFalse(dt.domain_enabled("software"))

    def test_53_unreadable_flag_file_fails_closed(self):
        self.flags.mkdir()
        try:
            self.assertFalse(dt.domain_enabled("game"))
        finally:
            self.flags.rmdir()

    def test_54_concurrent_thread_flag_writes_preserve_all(self):
        for dom in dt.DOMAINS:
            dt.set_domain_flag(dom, False)
        barrier = threading.Barrier(len(dt.DOMAINS))
        errors = []

        def work(dom):
            try:
                barrier.wait(timeout=10)
                dt.set_domain_flag(dom, True)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(d,))
                   for d in dt.DOMAINS]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        self.assertEqual(errors, [])
        for dom in dt.DOMAINS:
            self.assertTrue(dt.domain_enabled(dom), dom)

    def test_55_concurrent_process_flag_writes_preserve_all(self):
        import subprocess
        import sys
        for dom in dt.DOMAINS:
            dt.set_domain_flag(dom, False)
        code = '''
import sys, time
from pathlib import Path
import core.domain_teams as dt
dt._FLAGS = Path(sys.argv[1])
_orig = dt._load_flags_unlocked
def _slow():
    data = _orig()
    time.sleep(0.05)
    return data
dt._load_flags_unlocked = _slow
dt.set_domain_flag(sys.argv[2], True)
'''
        cwd = str(Path(__file__).resolve().parents[2])
        procs = [subprocess.Popen(
            [sys.executable, "-c", code, str(self.flags), d],
            cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for d in dt.DOMAINS]
        for proc, dom in zip(procs, dt.DOMAINS):
            _, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err.decode(errors="replace"))
        for dom in dt.DOMAINS:
            self.assertTrue(dt.domain_enabled(dom), dom)

    # -------------------------------- Finding F: classification inputs
    def test_56_malformed_classification_inputs_fail_closed(self):
        cases = (
            dict(product_type=123),
            dict(goal={"x": 1}),
            dict(capabilities="backend"),
            dict(capabilities={"backend": True}),
            dict(capabilities=["backend", 7]),
            dict(constraints="tight"),
            dict(constraints=["ok", None]),
            dict(project_context=[1, 2]),
        )
        for kw in cases:
            with self.subTest(kw=kw):
                args = dict(product_type="webpage", goal="ساخت صفحه وب")
                args.update(kw)
                r = dt.build_team(**args)
                self.assertFalse(r["ok"])
                self.assertTrue(r["problems"])
                self.assertEqual(r["team"], [])

    def test_57_invalid_risk_fails_closed_into_security_activation(self):
        for risk in ("extreme", 42):
            with self.subTest(risk=risk):
                r = self._team("webpage", "ساخت صفحه وب", risk=risk)
                self.assertTrue(r["ok"], r["problems"])
                self.assertTrue(r["context"]["risk_invalid"])
                self.assertEqual(r["context"]["risk"], "unknown")
                self.assertIn("security-reviewer", self._ids(r))
        game = self._team("game", "ساخت بازی اندرویدی", risk="extreme")
        self.assertTrue(game["ok"], game["problems"])
        self.assertIn("security-reviewer", self._ids(game))

    def test_58_metadata_only_constraints_and_context(self):
        base = self._team("webpage", "ساخت صفحه وب ساده")
        meta = self._team("webpage", "ساخت صفحه وب ساده",
                          constraints=["budget"],
                          project_context={"tenant": "x"})
        self.assertEqual(base["hash"], meta["hash"])
        self.assertEqual(meta["context"]["constraints"], ["budget"])
        self.assertEqual(meta["context"]["project_context"], {"tenant": "x"})

    # -------------------------------- Finding G: validate_team
    def test_59_dependency_after_consumer_rejected(self):
        a = self._contract_item("ui-ux-designer")
        b = self._contract_item("product-architect")
        bad = dt.validate_team([a, b])
        self.assertFalse(bad["ok"])
        self.assertTrue(any("dependency-order" in p
                            for p in bad["problems"]))
        self.assertTrue(dt.validate_team([b, a])["ok"])

    def test_60_forged_display_metadata_rejected(self):
        item = self._contract_item("product-architect")
        for key, value in (("title", "فورج"), ("domain", "game"),
                           ("stage_id", "evil")):
            with self.subTest(key=key):
                forged = dict(item)
                forged[key] = value
                report = dt.validate_team([forged])
                self.assertFalse(report["ok"])
                self.assertTrue(any("forged-" in p
                                    for p in report["problems"]))

    def test_61_duplicate_alias_role_rejected(self):
        item = self._contract_item("product-architect")
        alias = dict(item)
        alias["role_id"] = "product-director"
        report = dt.validate_team([item, alias])
        self.assertFalse(report["ok"])
        self.assertTrue(any("duplicate-role" in p
                            for p in report["problems"]))

    def test_62_malformed_collection_types_rejected(self):
        item = self._contract_item("developer")
        for field in ("expected_artifacts", "inputs", "dependencies",
                      "acceptance"):
            with self.subTest(field=field):
                forged = dict(item)
                forged[field] = "not-a-list"
                report = dt.validate_team([forged])
                self.assertFalse(report["ok"])
                self.assertTrue(any("malformed-field" in p
                                    for p in report["problems"]))

    # -------------------------------- Finding H: registry completeness
    def test_63_domain_roles_present_once_and_reconciled(self):
        from core import specialists
        ids = [r.id for r in specialists.DOMAIN_ROLES]
        self.assertEqual(len(ids), 16)
        self.assertEqual(len(ids), len(set(ids)))
        registry = se.canonical_registry()
        for rid in ids:
            self.assertIn(rid, registry)
        rep = se.catalog_reconciliation()
        self.assertTrue(rep["ok"], rep["problems"])
        self.assertIn("DOMAIN_ROLES", rep["sources"])

    def test_64_missing_domain_role_detected(self):
        from core import specialists
        registry = se.canonical_registry()
        missing = specialists.DOMAIN_ROLES[0].id
        registry.pop(missing, None)
        with mock.patch.object(se, "canonical_registry",
                               return_value=registry):
            rep = se.catalog_reconciliation()
        self.assertFalse(rep["ok"])
        self.assertTrue(any("missing-domain-role" in p
                            for p in rep["problems"]))


if __name__ == "__main__":
    unittest.main()
