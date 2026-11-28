"""Phase 07 E2E — real workflow -> producer -> critic -> revision -> decision.

No live provider, no real HTTP socket. API tests invoke the real server handler
methods with a deterministic fake request/response harness.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import confirmations, critique, panelauth, workflow
from core.tests import trusted_session

PROJECT = "p07-e2e"
STAGE = "s01-produce"


class HandlerHarness:
    """Real server handler methods, deterministic JSON request/response."""
    def __init__(self, body, session=""):
        self.body = body
        self.session = session
        self.responses = []

    def _read_json(self):
        return self.body

    def _error(self, code, message):
        self.responses.append((code, message))

    def _json(self, payload):
        self.responses.append((200, payload))

    def _cookie(self, name):
        return self.session


class E2ETest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        patches = [
            mock.patch.object(critique, "_LEDGER", root / "ledger.json"),
            mock.patch.object(critique, "_FLAGS", root / "flags.json"),
            mock.patch.object(confirmations, "_STORE", root / "confirm.json"),
            mock.patch.object(workflow, "_STORE", root / "workflow.json"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        critique.set_critic_flag(True)
        d = config.PROJECTS_DIR / PROJECT
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(
            json.dumps({"name": PROJECT, "type": "webapp"}))
        (d / "spec").mkdir(exist_ok=True)
        self.session = trusted_session()
        dag = [{"id": STAGE, "role": "developer", "title": "produce",
                "kind": "code", "deps": [], "tool": "text"}]
        workflow.create(PROJECT, "webapp", "build real artifact", dag=dag)

    def _executor(self, content):
        rel = "spec/proposal.md"
        def run(project, stage, goal):
            p = config.PROJECTS_DIR / project / rel
            p.write_text(content, encoding="utf-8")
            sha = workflow.sha256_file(rel, project)
            return {"status": "PASS", "artifacts": [
                {"path": rel, "sha256": sha, "size": p.stat().st_size}]}
        return run

    def _confirm(self, project, wid, review_id, path, sha, option, cycle,
                 rejected, evidence, session):
        return confirmations.create(project, "critic-decision", {
            "project": project, "workflow_id": wid, "stage_id": STAGE,
            "review_id": review_id, "artifact_path": path,
            "artifact_sha256": sha, "option": option, "cycle": cycle,
            "decision_fingerprint": critique.decision_fingerprint(
                project, wid, STAGE, review_id, sha, option, cycle,
                rejected, evidence)}, "q", session)["confirm_token"]

    def _produce(self, content="# real proposal\n"):
        out = workflow.run_stage(PROJECT, STAGE, "tok-producer",
                                 self._executor(content))
        self.assertEqual(out.get("status"), "PASS", out)
        return out

    # -------------------------------------------------------------- E2E path
    def test_01_full_series_accepts_a_real_artifact(self):
        self._produce()
        before = workflow.by_project(PROJECT)
        out = critique.run_series(PROJECT, STAGE)
        self.assertEqual(out["status"], "READY_FOR_HUMAN_DECISION", out)
        review = out["final"]["review"]
        latest = workflow.by_project(PROJECT)["stages"][0]["versions"][-1]
        self.assertEqual(review["artifact_sha256"],
                         latest["artifacts"][0]["sha256"])
        self.assertTrue(critique.entries(PROJECT, "verdict"))

    def test_02_empty_artifact_is_revised_then_reapproved(self):
        first = self._produce("")
        old_sha = first["artifacts"][0]["sha256"]
        # A prior human decision bound to v1 must be invalidated by revision.
        proposal = confirmations.create(PROJECT, "critic-decision", {
            "project": PROJECT, "workflow_id": "wf-1", "stage_id": STAGE,
            "review_id": "prior-review", "artifact_path": "spec/proposal.md",
            "artifact_sha256": old_sha, "option": "prior-choice", "cycle": 1},
            "q", self.session)
        critique.decide(PROJECT, "wf-1", STAGE, "prior-review",
                        "spec/proposal.md", old_sha, "prior-choice",
                        [{"option": "other", "reason": "lower", "owner": "user"}],
                        [], 1, proposal["confirm_token"], self.session)
        out = critique.run_series(PROJECT, STAGE)
        self.assertEqual(len(out["rounds"]), 2, out)
        stages = workflow.by_project(PROJECT)["stages"][0]
        self.assertEqual(len(stages["versions"]), 2)
        self.assertNotEqual(stages["versions"][0]["artifacts"][0]["sha256"],
                            stages["versions"][1]["artifacts"][0]["sha256"])
        self.assertEqual(stages["versions"][0]["artifacts"][0]["sha256"], old_sha)
        self.assertEqual(critique.artifact_hash(PROJECT, "spec/proposal.md"),
                         old_sha)   # previous version remains immutable
        new_path = stages["versions"][1]["artifacts"][0]["path"]
        self.assertNotEqual(critique.artifact_hash(PROJECT, new_path), old_sha)
        self.assertEqual(out["status"], "READY_FOR_HUMAN_DECISION", out)
        self.assertTrue(critique.entries(PROJECT, "invalidation"))

    def test_03_human_decision_advances_real_workflow(self):
        self._produce()
        series = critique.run_series(PROJECT, STAGE)
        review_id = series["final"]["review"]["id"]
        info = critique._stage_artifact(PROJECT, STAGE)
        token = self._confirm(PROJECT, info["workflow_id"], review_id,
                              info["path"], info["sha256"], "accept-current", 1,
                              [{"option": "revise",
                                "reason": "human selected the reviewed option",
                                "owner": "user"}], [info["path"]], self.session)
        out = critique.human_decision(PROJECT, STAGE, review_id,
                                      "accept-current", token, self.session)
        self.assertTrue(out["ok"])
        self.assertEqual(out["decision"]["decision_maker"],
                         panelauth.PANEL_PRINCIPAL)
        self.assertEqual(workflow.by_project(PROJECT)["stages"][0]["status"],
                         "APPROVED")

    def test_04_forged_decision_inputs_fail_closed(self):
        self._produce()
        series = critique.run_series(PROJECT, STAGE)
        rid = series["final"]["review"]["id"]
        with self.assertRaises(critique.CritiqueError):
            critique.human_decision(PROJECT, STAGE, rid, "accept-current",
                                    "forged", self.session)
        with self.assertRaises(critique.CritiqueError):
            critique.human_decision(PROJECT, STAGE, rid, "accept-current",
                                    "x", "invalid-session")

    def test_05_three_cycles_then_hold_no_fourth(self):
        self._produce("x")
        counter = {"n": 0}
        def always_revise(rubric, findings):
            counter["n"] += 1
            return {"ok": True, "aggregate": 5.0, "blockers": [], "blocked": False,
                    "decision": "revise", "rejected_options": [], "uncertainty": []}
        def changing_fix(content, finding, goal):
            counter["n"] += 1
            return (content + "\n## Real correction %d\n"
                    "Verified substantive content with inspectable sections "
                    "and a recorded evidence trail for this deliverable.\n" %
                    counter["n"])
        real_execute = critique.execute_review
        def forced_review(project, stage):
            out = real_execute(project, stage)
            f = critique.new_finding(
                out["review"]["id"], "critical", "forced revision",
                out["artifact_path"], out["version"], out["artifact_sha256"],
                "produce real content", critique.ROLE,
                criterion="meaningful-content",
                evidence={"path": out["artifact_path"],
                          "sha256": out["artifact_sha256"]})
            out["findings"] = [f]
            return out
        with mock.patch.object(critique, "evaluate", side_effect=always_revise), \
             mock.patch.object(critique, "apply_correction",
                               side_effect=changing_fix), \
             mock.patch.object(critique, "execute_review",
                               side_effect=forced_review):
            out = critique.run_series(PROJECT, STAGE)
        self.assertEqual(out["status"], "HOLD", out)
        self.assertEqual(len(out["rounds"]), 3, str(out.get("reason")) + " " + str(out.get("hold")))
        self.assertEqual(len(workflow.by_project(PROJECT)["stages"][0]["versions"]),
                         3)

    def test_06_finding_schema_and_evidence_rejected(self):
        self._produce()
        series = critique.run_series(PROJECT, STAGE)
        info = critique._stage_artifact(PROJECT, STAGE)
        bad = {"id": "f-forged", "severity": "critical", "description": "x",
               "evidence_ref": "none", "artifact_sha256": info["sha256"],
               "required_correction": "fix", "owner": "critic",
               "status": "open", "history": [{"status": "open"}],
               "evidence": {"path": "spec/proposal.md", "sha256": "bad"}}
        result = critique.validate_findings(PROJECT, [bad], info["path"],
                                            info["sha256"], [])
        self.assertFalse(result["ok"])
        self.assertTrue(any("evidence-hash-mismatch" in p or
                            "forged-finding-id" in p for p in result["problems"]))

    def test_07_conflict_detection_in_real_verdict(self):
        self._produce()
        with mock.patch.object(critique, "detect_conflicts", wraps=
                               critique.detect_conflicts) as spy:
            out = critique.run_series(PROJECT, STAGE)
        self.assertTrue(spy.called)
        self.assertTrue(out["ok"])

    # ------------------------------------------------------- API handler path
    def test_08_api_run_and_decide_handlers(self):
        from core import server
        self._produce()
        h = HandlerHarness({"project": PROJECT, "stage": STAGE})
        server.Handler._api_workflow_critique_run(h)
        code, payload = h.responses[-1]
        self.assertEqual(code, 200)
        series = payload["result"]
        self.assertEqual(series["status"], "READY_FOR_HUMAN_DECISION")
        info = critique._stage_artifact(PROJECT, STAGE)
        review_id = series["final"]["review"]["id"]
        token = self._confirm(PROJECT, info["workflow_id"], review_id,
                              info["path"], info["sha256"], "accept-current", 1,
                              [{"option": "revise",
                                "reason": "human selected the reviewed option",
                                "owner": "user"}], [info["path"]], self.session)
        h2 = HandlerHarness({"project": PROJECT, "stage": STAGE,
                             "review_id": review_id, "option": "accept-current",
                             "confirm_token": token}, self.session)
        server.Handler._api_workflow_critique_decide(h2)
        code, payload = h2.responses[-1]
        self.assertEqual(code, 200, payload)
        self.assertEqual(workflow.by_project(PROJECT)["stages"][0]["status"],
                         "APPROVED")

    def test_09_api_decide_rejects_missing_session(self):
        from core import server
        self._produce()
        h = HandlerHarness({"project": PROJECT, "stage": STAGE,
                            "review_id": "r", "option": "x",
                            "confirm_token": "x"}, "")
        server.Handler._api_workflow_critique_decide(h)
        self.assertEqual(h.responses[-1][0], 401)

    def test_10_restart_recovery_preserves_lineage(self):
        self._produce()
        out = critique.run_series(PROJECT, STAGE)
        before = critique.entries(PROJECT)
        # simulate process restart by clearing in-memory active state only
        workflow._ACTIVE.clear()
        after = critique.entries(PROJECT)
        self.assertEqual(len(before), len(after))
        self.assertTrue(all("seq" in e and "at" in e for e in after))
        self.assertTrue(out["ok"])


if __name__ == "__main__":
    unittest.main()
