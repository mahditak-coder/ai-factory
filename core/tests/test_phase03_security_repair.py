"""Phase 03 audit repair — verification truth, atomic approval, path safety, scoring."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import confirmations, panelauth, research_product as rp

VALID_SESSION = "valid-panel-session"


class Phase03SecurityRepairTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = self.base / "strategy.json"
        self.conf = self.base / "confirmations.json"
        self.conf.write_text(json.dumps({"items": {}}), encoding="utf-8")
        self.patches = [mock.patch.object(rp, "_STORE", self.store),
                        mock.patch.object(confirmations, "_STORE", self.conf),
                        mock.patch.object(panelauth, "principal_for_session",
                                          side_effect=lambda t: (
                                              panelauth.PANEL_PRINCIPAL
                                              if t == VALID_SESSION else None))]
        for p in self.patches:
            p.start()
        for name in ("p3-secure", "p3-other"):
            d = config.PROJECTS_DIR / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "meta.json").write_text('{"name":"%s","type":"webapp"}' % name,
                                         encoding="utf-8")
        self.project = "p3-secure"

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _source(self, url="https://a.invalid/x", claim="ادعا", typ="verified_fact",
                topic="topic"):
        return rp.add_source(self.project, url, "عنوان", claim, typ, topic=topic)

    def _criteria(self):
        return [{"id": "ac-1", "description": "تست", "measurable": True,
                 "testable": True, "linked_source": "local", "status": "unknown"}]

    def _brief(self, project=None):
        return rp.create_brief(project or self.project, "req-1",
                               sections={"problem": "مشکل",
                                         "acceptance_criteria": self._criteria()})

    # ------------------------------------------ 1. verification truth model
    def test_caller_claimed_verified_fact_never_verified(self):
        s = self._source(typ="verified_fact")
        self.assertEqual(s["claimed_type"], "verified_fact")
        self.assertEqual(s["verification_status"], "unverified")
        self.assertIsNone(s["provenance"]["verification_method"])
        self.assertIsNone(s["provenance"]["verified_at"])
        self.assertIsNone(s["provenance"]["verifier"])

    def test_invalid_domain_never_verified(self):
        s = self._source(url="https://example.invalid/a")
        self.assertEqual(s["verification_status"], "unverified")
        self.assertEqual(s["verification"]["reason"],
                         "reserved-or-unreachable-url")

    def test_unchecked_url_never_sufficient(self):
        self._source(url="https://unreachable.example/x")
        report = rp.assess_sources(self.project)
        self.assertFalse(report["sufficient"])
        self.assertEqual(report["verified_count"], 0)

    def test_forged_verification_fields_rejected(self):
        with self.assertRaisesRegex(rp.ResearchError, "forged-source-field"):
            rp.add_source(self.project, "https://a.invalid", "t", "c",
                          verification_status="verified")
        with self.assertRaisesRegex(rp.ResearchError, "forged-source-field"):
            rp.add_source(self.project, "https://a.invalid", "t", "c",
                          provenance={"verifier": "fake"})

    def test_no_verifier_fails_closed_and_blocks_auto_select(self):
        s = self._source()
        rp.add_alternative(self.project, "only", "only", "x",
                           criteria={"cost": 5, "complexity": 5, "risk": 5,
                                     "time": 5, "feasibility": 5},
                           evidence_source_ids=[s["source_id"]])
        result = rp.compare_alternatives(self.project, auto_select=True)
        self.assertIsNone(result["selected"])
        self.assertTrue(result["auto_selection_blocked"])
        self.assertIn("insufficient-evidence", result["blocked_reason"])

    def test_verified_and_unverified_separated(self):
        self._source(url="https://a.invalid/1", claim="ad1", typ="verified_fact")
        self._source(url="https://a.invalid/2", claim="ad2", typ="hypothesis")
        report = rp.assess_sources(self.project)
        self.assertEqual(report["verified"], [])
        self.assertEqual(len(report["unverified"]), 2)
        self.assertEqual(report["by_claimed_type"],
                         {"verified_fact": 1, "hypothesis": 1})

    def test_claim_types_remain_semantically_distinct(self):
        for typ in rp.CLAIM_TYPES:
            s = self._source(url="https://a.invalid/" + typ, typ=typ)
            self.assertEqual(s["claimed_type"], typ)
            self.assertEqual(s["verification_status"], "unverified")

    # ------------------------------------------- 2. atomic brief approval
    def _approve(self, bid, token, actor="user", session="", project=None):
        return rp.approve_brief(bid, token, actor, session_id=session)

    def test_concurrent_revise_and_approval(self):
        b = self._brief()
        prop = rp.propose_brief_approval(b["brief_id"])
        barrier = threading.Barrier(2)
        outcomes = []

        def revise():
            barrier.wait(timeout=10)
            try:
                rp.revise_brief(b["brief_id"], sections={"goal": "جدید"},
                                reason="تغییر")
                outcomes.append("revise-ok")
            except rp.ResearchError as exc:
                outcomes.append("revise-err:" + str(exc))

        def approve():
            barrier.wait(timeout=10)
            try:
                self._approve(b["brief_id"], prop["confirm_token"])
                outcomes.append("approve-ok")
            except rp.ResearchError as exc:
                outcomes.append("approve-err:" + str(exc))

        threads = [threading.Thread(target=revise),
                   threading.Thread(target=approve)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        self.assertEqual(len(outcomes), 2)
        self.assertLessEqual(outcomes.count("approve-ok"), 1)
        self.assertLessEqual(outcomes.count("revise-ok"), 1)
        if "approve-ok" in outcomes:
            self.assertEqual(rp.brief(b["brief_id"])["status"], "USER_APPROVED")
        else:
            new_id = "brief-p3-secure-v2"
            self.assertIsNotNone(rp.brief(new_id))
            self.assertEqual(rp.brief(new_id)["status"], "PENDING_USER_APPROVAL")

    def test_two_concurrent_approvals_single_winner(self):
        b = self._brief()
        prop = rp.propose_brief_approval(b["brief_id"])
        barrier = threading.Barrier(2)
        outcomes = []

        def worker():
            barrier.wait(timeout=10)
            try:
                self._approve(b["brief_id"], prop["confirm_token"])
                outcomes.append("ok")
            except rp.ResearchError:
                outcomes.append("err")

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        self.assertEqual(outcomes.count("ok"), 1)
        self.assertEqual(outcomes.count("err"), 1)

    def test_old_hash_and_status_change_rejected(self):
        b = self._brief()
        prop = rp.propose_brief_approval(b["brief_id"])
        rp.revise_brief(b["brief_id"], sections={"goal": "x"}, reason="x")
        with self.assertRaises(rp.ResearchError):
            self._approve(b["brief_id"], prop["confirm_token"])
        b2 = self._brief()
        prop2 = rp.propose_brief_approval(b2["brief_id"])
        self._approve(b2["brief_id"], prop2["confirm_token"])
        with self.assertRaisesRegex(rp.ResearchError, "not-pending"):
            rp.propose_brief_approval(b2["brief_id"])

    def test_token_replay_and_wrong_project_rejected(self):
        b = self._brief()
        prop = rp.propose_brief_approval(b["brief_id"])
        self._approve(b["brief_id"], prop["confirm_token"])
        with self.assertRaises(rp.ResearchError):
            self._approve(b["brief_id"], prop["confirm_token"])
        other = self._brief("p3-other")
        with self.assertRaises(rp.ResearchError):
            self._approve(other["brief_id"], prop["confirm_token"])

    def test_stale_approval_never_migrates_to_new_version(self):
        b = self._brief()
        old_token = rp.propose_brief_approval(b["brief_id"])["confirm_token"]
        new = rp.revise_brief(b["brief_id"], sections={"goal": "نو"}, reason="نو")
        with self.assertRaises(rp.ResearchError):
            self._approve(new["brief_id"], old_token)
        self.assertEqual(rp.brief(new["brief_id"])["status"],
                         "PENDING_USER_APPROVAL")

    def test_session_identity_and_declared_actor(self):
        b = self._brief()
        prop = rp.propose_brief_approval(b["brief_id"],
                                         session_id=VALID_SESSION)
        with self.assertRaises(rp.ResearchError):
            self._approve(b["brief_id"], prop["confirm_token"], session="")
        approved = self._approve(b["brief_id"], prop["confirm_token"],
                                 actor="fabricated", session=VALID_SESSION)
        self.assertTrue(approved["user_approval"]["actor_trusted"])
        self.assertEqual(approved["user_approval"]["actor"],
                         panelauth.PANEL_PRINCIPAL)
        self.assertEqual(approved["user_approval"]["declared_actor"],
                         "fabricated")
        b2 = self._brief()
        p2 = rp.propose_brief_approval(b2["brief_id"])
        granted = self._approve(b2["brief_id"], p2["confirm_token"],
                                actor="self-declared")
        self.assertFalse(granted["user_approval"]["actor_trusted"])
        self.assertEqual(granted["user_approval"]["identity_mode"],
                         "self-declared")

    # ------------------------------------------- 3. path traversal safety
    def _set_brief_project(self, bid, project):
        data = json.loads(self.store.read_text(encoding="utf-8"))
        data["briefs"][bid]["project"] = project
        self.store.write_text(json.dumps(data), encoding="utf-8")

    def test_path_traversal_rejected_without_side_effect(self):
        b = self._brief()
        for bad in ("../escape", "a/../../escape", "/tmp/absolute", "", "."):
            with self.subTest(bad=bad):
                self._set_brief_project(b["brief_id"], bad)
                with self.assertRaises(rp.ResearchError):
                    rp.write_brief_artifact(b["brief_id"])
        self.assertFalse((config.PROJECTS_DIR.parent / "escape").exists())

    def test_symlink_escape_rejected(self):
        outside = self.base / "outside"
        outside.mkdir()
        link = config.PROJECTS_DIR / "p3-link"
        if not link.exists():
            link.symlink_to(outside, target_is_directory=True)
        try:
            b = self._brief()
            self._set_brief_project(b["brief_id"], "p3-link")
            with self.assertRaisesRegex(rp.ResearchError, "symlink"):
                rp.write_brief_artifact(b["brief_id"])
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            if link.is_symlink():
                link.unlink()

    def test_valid_path_still_writes_artifact(self):
        b = self._brief()
        art = rp.write_brief_artifact(b["brief_id"])
        self.assertTrue((config.PROJECT_ROOT / art["path"]).is_file())
        self.assertEqual(art["brief_id"], b["brief_id"])

    # ---------------------------------------------- 4. utility orientation
    def test_utility_direction_higher_is_better(self):
        rp.add_alternative(self.project, "cheap", "cheap", "x",
                           criteria={"cost": 5, "complexity": 4, "risk": 5,
                                     "time": 4, "feasibility": 5})
        rp.add_alternative(self.project, "expensive", "expensive", "x",
                           criteria={"cost": 1, "complexity": 2, "risk": 1,
                                     "time": 1, "feasibility": 2})
        rows = {r["option_id"]: r for r in
                rp.compare_alternatives(self.project)["options"]}
        self.assertGreater(rows["cheap"]["score_total"],
                           rows["expensive"]["score_total"])
        self.assertEqual(rows["cheap"]["score_direction"], "higher_is_better")
        self.assertEqual(rows["cheap"]["utilities"]["cost_utility"], 5)

    def test_raw_cost_value_is_not_summed_as_amount(self):
        rec = rp.add_alternative(self.project, "raw", "raw", "x",
                                 criteria={"cost": 5})
        self.assertIn("score_scale", rec)
        self.assertNotIn("raw_cost", rec)

    def test_option_name_survives_utility_scoring_and_comparison(self):
        name_a, name_b = "نام متمایز گزینهٔ الف", "نام متمایز گزینهٔ ب"
        rp.add_alternative(self.project, "opt-alpha", name_a, "x",
                           criteria={"cost": 5, "complexity": 4, "risk": 3,
                                     "time": 2, "feasibility": 1})
        rp.add_alternative(self.project, "opt-beta", name_b, "y",
                           criteria={"cost": 1, "complexity": 2, "risk": 3,
                                     "time": 4, "feasibility": 5})
        stored = json.loads(self.store.read_text(encoding="utf-8"))
        self.assertEqual(stored["alternatives"]["opt-alpha"]["name"], name_a)
        self.assertEqual(stored["alternatives"]["opt-beta"]["name"], name_b)
        self.assertEqual(len(stored["alternatives"]["opt-alpha"]["utility_scores"]), 5)
        rows = {r["option_id"]: r for r in
                rp.compare_alternatives(self.project)["options"]}
        self.assertEqual(rows["opt-alpha"]["name"], name_a)
        self.assertEqual(rows["opt-beta"]["name"], name_b)
        for bad in ("cost_utility", "feasibility_utility", "risk_utility",
                    "complexity_utility", "time_utility"):
            self.assertNotEqual(rows["opt-alpha"]["name"], bad)
            self.assertNotEqual(rows["opt-beta"]["name"], bad)


if __name__ == "__main__":
    unittest.main()
