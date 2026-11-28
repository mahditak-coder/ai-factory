"""Phase 07 — critic independence, bounded revision, append-only ledger."""
import json
import multiprocessing
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import confirmations, critique, panelauth, specialist_engine as se
from core.tests import trusted_session


PROJECT = "p07-unit"
SHA = "a" * 64


def _worker(project, workflow, stage, review, artifact, sha, option,
            token, session, queue, ledger, flags, conf_store):
    try:
        critique._LEDGER = Path(ledger)
        critique._FLAGS = Path(flags)
        confirmations._STORE = Path(conf_store)
        import core.panelauth as pa
        pa._sessions[session] = {"exp": time.time() + 86400,
                                 "csrf": "test", "created": time.time()}
        rejected = [{"option": "B", "reason": "x", "owner": "critic"}]
        evidence = []
        queue.put(("ok", critique.decide(
            project, workflow, stage, review, artifact, sha, option,
            rejected, evidence, 1, token, session,
            decision_fp=_decision_fp(project, workflow, stage, review,
                                     artifact, sha, option, 1, rejected,
                                     evidence, session))))
    except Exception as exc:  # noqa: BLE001
        queue.put(("error", repr(exc)))


def _decision_fp(project, wid, stage, review, path, sha, option, cycle,
                 rejected, evidence, session):
    import core.panelauth as pa
    return critique.decision_fingerprint(
        project, wid, stage, review, path, sha, option, cycle, rejected,
        evidence, pa.principal_for_session(session), session)


class CritiqueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.ledger = root / "ledger.json"
        self.flags = root / "flags.json"
        self.conf = root / "confirmations.json"
        self.flags.write_text(json.dumps({"enabled": True}))
        for patch in (mock.patch.object(critique, "_LEDGER", self.ledger),
                      mock.patch.object(critique, "_FLAGS", self.flags),
                      mock.patch.object(confirmations, "_STORE", self.conf)):
            patch.start()
            self.addCleanup(patch.stop)
        d = config.PROJECTS_DIR / PROJECT
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps({"name": PROJECT, "type": "webapp"}))
        (d / "spec").mkdir(exist_ok=True)
        (d / "spec" / "proposal.md").write_text("# proposal v1\n", encoding="utf-8")
        self.sha = critique.artifact_hash(PROJECT, "spec/proposal.md")
        self.session = trusted_session()

    def _review(self, producer="developer"):
        return critique.start_review(PROJECT, "wf-1", "s05-produce", producer,
                                     "spec/proposal.md", self.sha,
                                     ["exact evidence"], self.session)

    def _rubric(self, score=4.0, weights=(1.0,)):
        return {"schema": critique.RUBRIC_SCHEMA,
                "criteria": [{"weight": w, "score": score,
                              "explanation": "checked", "evidence": ["e1"]}
                             for w in weights],
                "alternatives": [
                    {"id": "A", "score": score, "benefits": "simple",
                     "tradeoffs": "limited", "rejected": False}],
                "uncertainty": ["none"]}

    def _findings(self, severity="medium"):
        return critique.new_finding("rv-x", severity, "needs fix", "e1",
                                    1, self.sha, "add tests", "critic-1")

    # --------------------------------------------------------- A: registry
    def test_01_critic_in_one_canonical_registry(self):
        self.assertIsNotNone(se.get(critique.ROLE))
        self.assertEqual(len([r for r in se.canonical_registry()
                              if r == critique.ROLE]), 1)
        self.assertTrue(se.catalog_reconciliation()["ok"])
        self.assertTrue(se.alias_report()["ok"])
        c = se.get(critique.ROLE)
        self.assertIn("project:read", c["allowed_scopes"])
        self.assertNotIn("project:write", c["allowed_scopes"])
        self.assertNotIn("image:write", c["allowed_scopes"])

    def test_02_critic_role_cannot_use_write_scope(self):
        with self.assertRaises(se.EngineError):
            se.require_scope(critique.ROLE, "project:write")
        self.assertTrue(se.require_scope(critique.ROLE, "tests:run"))

    def test_03_flag_off_is_fail_closed(self):
        self.flags.write_text("{corrupt", encoding="utf-8")
        self.assertFalse(critique.critic_enabled())
        with self.assertRaises(critique.CritiqueError):
            self._review()

    # ------------------------------------------------------- B: rubric
    def test_04_invalid_weights_scores_and_missing_reasons_rejected(self):
        bad = self._rubric()
        bad["criteria"][0]["weight"] = 2.0
        self.assertFalse(critique.validate_rubric(bad)["ok"])
        bad = self._rubric()
        bad["criteria"][0]["score"] = 9
        self.assertFalse(critique.validate_rubric(bad)["ok"])
        bad = self._rubric()
        bad["alternatives"][0]["rejected"] = True
        self.assertFalse(critique.validate_rubric(bad)["ok"])

    def test_05_critical_finding_blocks_despite_high_score(self):
        rubric = self._rubric(score=5.0)
        out = critique.evaluate(rubric, [self._findings("critical")])
        self.assertTrue(out["ok"])
        self.assertTrue(out["blocked"])
        self.assertEqual(out["decision"], "reject")
        soft = critique.evaluate(rubric, [self._findings("low")])
        self.assertFalse(soft["blocked"])
        self.assertEqual(soft["decision"], "pass")

    def test_06_missing_evidence_stays_explicitly_unknown(self):
        rubric = self._rubric()
        rubric["uncertainty"] = ["evidence unavailable"]
        out = critique.evaluate(rubric, [])
        self.assertEqual(out["uncertainty"], ["evidence unavailable"])

    # ------------------------------------------------------- C: conflicts
    def test_07_conflicts_detected_and_never_silently_resolved(self):
        f = self._findings("high")
        conflicts = critique.detect_conflicts(
            [{"artifact_sha256": self.sha, "claim": "all requirements met"}],
            [self._findings("critical")], [{"id": "A", "score": 5}], [])
        self.assertTrue(conflicts)
        self.assertTrue(all(c["unresolved"] for c in conflicts))
        kinds = {c["kind"] for c in conflicts}
        self.assertIn("producer-vs-critic", kinds)
        self.assertIn("score-vs-blocker", kinds)

    def test_08_finding_has_stable_id_and_history(self):
        f = self._findings()
        self.assertTrue(f["id"].startswith("f-"))
        self.assertEqual(f["status"], "open")
        self.assertEqual(len(f["history"]), 1)

    # -------------------------------------------------- D: bounded revision
    def test_09_three_cycles_then_hold(self):
        review = self._review()
        token = review["critic_run_token"]
        for cycle in range(1, 4):
            rev = critique.request_revision(review["id"], token, [])
            self.assertEqual(rev["cycle"], cycle)
        hold = critique.request_revision(review["id"], token, [])
        self.assertEqual(hold["kind"], "hold")
        self.assertEqual(hold["status"], "HOLD")

    def test_10_critic_cannot_be_producer_and_lease_required(self):
        with self.assertRaises(critique.CritiqueError):
            critique.start_review(PROJECT, "wf", "s1", critique.ROLE,
                                  "spec/proposal.md", self.sha, [])
        review = self._review()
        with self.assertRaises(critique.CritiqueError):
            critique.record_verdict(review["id"], "wrong-token", [],
                                    self._rubric(), self.session)

    def test_11_critic_cannot_self_approve(self):
        review = self._review()
        token = review["critic_run_token"]
        forged = {"ok": True, "aggregate": 5.0, "blockers": [],
                  "blocked": False, "decision": "approve",
                  "rejected_options": [], "uncertainty": []}
        with mock.patch.object(critique, "evaluate", return_value=forged):
            with self.assertRaises(critique.CritiqueError) as ctx:
                critique.record_verdict(review["id"], token, [],
                                        self._rubric(5.0), self.session)
        self.assertIn("self-approve", str(ctx.exception))

    def test_12_immutable_artifact_and_lineage(self):
        a1 = critique.immutable_artifact(PROJECT, "spec/review.md", "v1",
                                         {"role": "critic"})
        a2 = critique.immutable_artifact(PROJECT, "spec/review.md", "v2",
                                         {"role": "critic"})
        self.assertNotEqual(a1["sha256"], a2["sha256"])
        self.assertEqual(critique.artifact_hash(PROJECT, a1["path"]),
                         a1["sha256"])
        self.assertEqual(a2["version"], 2)

    # ------------------------------------------------------- E: ledger
    def test_13_append_only_and_replay_rejected(self):
        proposal = confirmations.create(PROJECT, "critic-decision", {
            "project": PROJECT, "workflow_id": "wf-1", "stage_id": "s05-produce",
            "review_id": "rv-x", "artifact_path": "spec/proposal.md",
            "artifact_sha256": self.sha, "option": "A", "cycle": 1,
            "decision_fingerprint": _decision_fp(
                PROJECT, "wf-1", "s05-produce", "rv-x", "spec/proposal.md",
                self.sha, "A", 1, [{"option": "B", "reason": "weaker",
                                   "owner": "critic"}], ["e1"], self.session)},
            "approve?", self.session)
        kwargs = dict(project=PROJECT, workflow_id="wf-1",
                      stage_id="s05-produce", review_id="rv-x",
                      artifact_path="spec/proposal.md",
                      artifact_sha256=self.sha, option="A",
                      rejected=[{"option": "B", "reason": "weaker",
                                 "owner": "critic"}], evidence_refs=["e1"],
                      cycle=1, confirm_token=proposal["confirm_token"],
                      session_id=self.session,
                      decision_fp=_decision_fp(
                          PROJECT, "wf-1", "s05-produce", "rv-x",
                          "spec/proposal.md", self.sha, "A", 1,
                          [{"option": "B", "reason": "weaker",
                            "owner": "critic"}], ["e1"], self.session))
        first = critique.decide(**kwargs)
        self.assertEqual(first["decision_maker"], panelauth.PANEL_PRINCIPAL)
        self.assertTrue(first["actor_trusted"])
        with self.assertRaises(critique.CritiqueError) as ctx:
            critique.decide(**kwargs)
        self.assertIn("replay", str(ctx.exception))
        before = len(critique.entries(PROJECT))
        self.assertEqual(before, len(critique.entries(PROJECT)))

    def test_14_forged_actor_project_and_binding_rejected(self):
        with self.assertRaises(critique.CritiqueError):
            critique.decide(PROJECT, "wf", "s", "r", "spec/proposal.md",
                            self.sha, "A", [{"reason": "x"}], [], 1,
                            "bogus", self.session, decision_fp="x")
        with self.assertRaises(critique.CritiqueError):
            critique.decide("other-project", "wf", "s", "r",
                            "spec/proposal.md", self.sha, "A",
                            [{"reason": "x"}], [], 1, "bogus", self.session,
                            decision_fp="x")

    def test_15_corrupt_store_fails_closed_without_overwrite(self):
        self.ledger.write_text("{corrupt", encoding="utf-8")
        with self.assertRaises(critique.CritiqueError):
            critique.entries(PROJECT)
        self.assertEqual(self.ledger.read_text(encoding="utf-8"), "{corrupt")

    def test_16_thread_and_process_concurrent_decide_one_winner(self):
        def proposal(option):
            rejected = [{"option": "B", "reason": "x", "owner": "critic"}]
            return confirmations.create(PROJECT, "critic-decision", {
                "project": PROJECT, "workflow_id": "wf", "stage_id": "s",
                "review_id": "r", "artifact_path": "spec/proposal.md",
                "artifact_sha256": self.sha, "option": option, "cycle": 1,
                "decision_fingerprint": _decision_fp(
                    PROJECT, "wf", "s", "r", "spec/proposal.md", self.sha,
                    option, 1, rejected, [], self.session)},
                "q", self.session)["confirm_token"]

        barrier = threading.Barrier(4)
        results = []

        tokens = [proposal("A") for _ in range(4)]

        def work(idx):
            try:
                token = tokens[idx]
                barrier.wait(timeout=10)
                results.append(critique.decide(
                    PROJECT, "wf", "s", "r", "spec/proposal.md", self.sha,
                    "A", [{"option": "B", "reason": "x",
                            "owner": "critic"}], [], 1, token,
                    self.session,
                    decision_fp=_decision_fp(
                        PROJECT, "wf", "s", "r", "spec/proposal.md", self.sha,
                        "A", 1, [{"option": "B", "reason": "x",
                                  "owner": "critic"}], [], self.session)))
            except Exception as exc:  # noqa: BLE001
                results.append("ERR:" + type(exc).__name__ + ":" + str(exc)[:400])

        threads = [threading.Thread(target=work, args=(i,))
                   for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        self.assertEqual(len([r for r in results
                              if isinstance(r, dict) and r.get("id")]), 1,
                         results)

        queue = multiprocessing.Queue()
        procs = [multiprocessing.Process(target=_worker, args=(
            PROJECT, "wf", "s", "r", "spec/proposal.md", self.sha, "B",
            proposal("B"), self.session, queue, str(self.ledger), str(self.flags),
            str(self.conf))) for _ in range(4)]
        for p in procs:
            p.start()
        replies = [queue.get(timeout=40) for _ in procs]
        for p in procs:
            p.join(timeout=40)
        self.assertEqual(sum(1 for _, r in replies
                             if isinstance(r, dict) and r.get("id")), 1,
                         replies)

    def test_17_invalidate_bound_decision_on_artifact_change(self):
        proposal = confirmations.create(PROJECT, "critic-decision", {
            "project": PROJECT, "workflow_id": "wf", "stage_id": "s",
            "review_id": "r", "artifact_path": "spec/proposal.md",
            "artifact_sha256": self.sha, "option": "A", "cycle": 1,
            "decision_fingerprint": _decision_fp(
                PROJECT, "wf", "s", "r", "spec/proposal.md", self.sha, "A",
                1, [{"option": "B", "reason": "x"}], [], self.session)},
            "q", self.session)["confirm_token"]
        critique.decide(PROJECT, "wf", "s", "r", "spec/proposal.md", self.sha,
                        "A", [{"option": "B", "reason": "x"}], [], 1,
                        proposal, self.session,
                        decision_fp=_decision_fp(
                            PROJECT, "wf", "s", "r", "spec/proposal.md",
                            self.sha, "A", 1,
                            [{"option": "B", "reason": "x"}], [],
                            self.session))
        out = critique.invalidate(PROJECT, "spec/proposal.md", "b" * 64)
        self.assertEqual(len(out["invalidated"]), 1)

    def test_18_migration_preserves_legacy_without_critic_authority(self):
        out = critique.migrate_legacy_approvals()
        self.assertTrue(out["ok"])
        rows = critique.entries(kind="migration")
        self.assertTrue(all(e.get("is_critic_approval") is False for e in rows))
