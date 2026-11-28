"""Phase 07 final closure — regression tests for the re-audit findings."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import confirmations, critique, panelauth, workflow
from core.tests import trusted_session

PROJECT = "p07-f2"
STAGE = "s01-produce"
REL = "spec/proposal.md"


class ClosureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for patch in (
            mock.patch.object(critique, "_LEDGER", root / "ledger.json"),
            mock.patch.object(critique, "_FLAGS", root / "flags.json"),
            mock.patch.object(confirmations, "_STORE", root / "confirm.json"),
            mock.patch.object(workflow, "_STORE", root / "workflow.json"),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        critique.set_critic_flag(True)
        d = config.PROJECTS_DIR / PROJECT
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps({"name": PROJECT, "type": "webapp"}))
        (d / "spec").mkdir(exist_ok=True)
        self.session = trusted_session()
        workflow.create(PROJECT, "webapp", "build real artifact with evidence",
                        dag=[{"id": STAGE, "role": "developer", "title": "p",
                              "kind": "code", "deps": []}])
        self._produce(self._good("build real artifact with evidence"))

    def _good(self, goal):
        return ("# Deliverable\n\n## Goal\n" + goal +
                "\n\n## Evidence\nReal content with inspectable sections and "
                "verified SHA-256 for this deliverable.\n")

    def _produce(self, text):
        (config.PROJECTS_DIR / PROJECT / REL).write_text(text, encoding="utf-8")
        out = workflow.run_stage(PROJECT, STAGE, "tok", self._executor())
        self.assertEqual(out.get("status"), "PASS", str(out))

    def _executor(self):
        def run(project, stage, goal):
            return {"status": "PASS", "artifacts": [
                {"path": REL, "sha256": workflow.sha256_file(REL, project)}]}
        return run

    def _producer(self, text):
        def run(project, stage, goal):
            (config.PROJECTS_DIR / project / REL).write_text(
                text, encoding="utf-8")
            return {"status": "PASS", "artifacts": [
                {"path": REL, "sha256": workflow.sha256_file(REL, project)}]}
        return run

    def _set_content(self, text):
        (config.PROJECTS_DIR / PROJECT / REL).write_text(text, encoding="utf-8")
        sha = workflow.sha256_file(REL, PROJECT)
        with workflow._LOCK:
            items = workflow._load()
            rec = next(iter(items.values()))
            rec["stages"][0]["versions"][-1]["artifacts"][0]["sha256"] = sha
            workflow._save(items)
        return sha

    def _info(self):
        return critique._stage_artifact(PROJECT, STAGE)

    def _ready(self):
        out = critique.run_series(PROJECT, STAGE, self._producer(
            self._good("build real artifact with evidence")))
        self.assertEqual(out["status"], "READY_FOR_HUMAN_DECISION", out)
        return out["final"]["review"]["id"]

    def _token(self, rid, sha=None, version=None, workflow_id=None,
               rejected=None, evidence=None, cycle=1, session=None):
        info = self._info()
        s = session or self.session
        wid = workflow_id or info["workflow_id"]
        sha = sha or info["sha256"]
        ver = version if version is not None else info["version_no"]
        rejected = rejected if rejected is not None else [
            {"option": "revise", "reason": "human selected the reviewed option",
             "owner": "user"}]
        evidence = evidence if evidence is not None else [info["path"]]
        return confirmations.create(PROJECT, "critic-decision", {
            "project": PROJECT, "workflow_id": wid, "stage_id": STAGE,
            "review_id": rid, "artifact_path": info["path"],
            "artifact_sha256": sha, "option": "accept-current", "cycle": cycle,
            "decision_fingerprint": critique.decision_fingerprint(
                PROJECT, wid, STAGE, rid, info["path"], sha, ver,
                "accept-current", cycle, rejected, evidence,
                panelauth.principal_for_session(s), s)}, "q", s
        )["confirm_token"]

    # ------------------------------------------------------------ approval
    def test_01_direct_approve_without_decision_is_blocked(self):
        r = workflow.approve(PROJECT, STAGE, 1, "forged")
        self.assertFalse(r.get("ok"))
        self.assertIn("governed-decision", r.get("error", ""))

    def test_02_decide_rejects_unknown_review_and_fake_verdict(self):
        sha = self._info()["sha256"]
        token = self._token("rv-fake", sha=sha)
        with self.assertRaises(critique.CritiqueError):
            critique.human_decision(PROJECT, STAGE, "rv-fake",
                                    "accept-current", token, self.session)
        self.assertFalse(critique.entries(PROJECT, "decision"))

    def test_03_old_review_rejected_for_new_workflow(self):
        rid = self._ready()
        token = self._token(rid, workflow_id="wf-other")
        with self.assertRaises(critique.CritiqueError):
            critique.human_decision(PROJECT, STAGE, rid, "accept-current",
                                    token, self.session)

    # -------------------------------------------------------------- verdict
    def test_04_failed_required_criterion_cannot_be_averaged(self):
        rubric = {"schema": critique.RUBRIC_SCHEMA,
                  "criteria": [
                      {"id": "non-empty", "criterion": "non-empty",
                       "weight": 0.9, "score": 5, "explanation": "ok",
                       "evidence": [REL], "status": "VERIFIED"},
                      {"id": "meaningful-content",
                       "criterion": "meaningful-content", "weight": 0.1,
                       "score": 0, "explanation": "failed",
                       "evidence": [REL], "status": "FAILED"}],
                  "alternatives": [{"id": "A", "score": 4.5,
                                    "benefits": "b", "tradeoffs": "t",
                                    "rejected": False}],
                  "uncertainty": []}
        out = critique.evaluate(rubric, [])
        self.assertTrue(out["blocked"])
        self.assertNotEqual(out["decision"], "pass")

    def test_05_unresolved_conflict_blocks_approval(self):
        rid = self._ready()
        critique._append({"id": "c-manual", "kind": "conflict",
                          "project": PROJECT, "stage_id": STAGE,
                          "review_id": rid, "unresolved": True,
                          "severity": "critical", "reason": "contradiction"})
        token = self._token(rid)
        with self.assertRaises(critique.CritiqueError) as ctx:
            critique.human_decision(PROJECT, STAGE, rid, "accept-current",
                                    token, self.session)
        self.assertIn("unresolved-conflict", str(ctx.exception))

    def test_06_forged_rubric_score_rejected(self):
        sha = self._set_content("TODO placeholder TBD Lorem ipsum sample text")
        rv = critique.start_review(PROJECT, self._info()["workflow_id"],
                                   STAGE, "developer", REL, sha,
                                   ["meaningful-content"], artifact_version=1)
        rubric = {"schema": critique.RUBRIC_SCHEMA,
                  "criteria": [{"id": "meaningful-content",
                                "criterion": "meaningful-content",
                                "weight": 1.0, "score": 5,
                                "explanation": "fabricated",
                                "evidence": [REL], "status": "VERIFIED"}],
                  "alternatives": [{"id": "A", "score": 5, "benefits": "b",
                                    "tradeoffs": "t", "rejected": False}],
                  "uncertainty": []}
        with self.assertRaises(critique.CritiqueError):
            critique.record_verdict(rv["id"], rv["critic_run_token"], [],
                                    rubric)

    # ---------------------------------------------------------- multilingual
    def test_07_persian_goal_and_output_supported(self):
        good = ("# سند\n\n## هدف\nساخت سند واقعی با شواهد معتبر و بررسی "
                "محتوای واقعی برای این تحویل.\n\n## شواهد\nهمه موارد بررسی شد.\n")
        self.assertTrue(critique.is_task_relevant(
            good, "ساخت سند واقعی با شواهد", [
                {"criterion": "meaningful-content",
                 "required_correction": "محتوای واقعی"}]))

    def test_08_keyword_stuffing_rejected(self):
        stuffed = "# Deliverable\n\n" + ("evidence " * 40)
        self.assertFalse(critique.is_task_relevant(
            stuffed, "build a real deliverable", []))

    def test_09_criteria_change_creates_new_review(self):
        first = critique.execute_review(PROJECT, STAGE)
        with workflow._LOCK:
            items = workflow._load()
            rec = next(iter(items.values()))
            rec["stages"][0]["acceptance_criteria"] = ["has-sections"]
            workflow._save(items)
        second = critique.execute_review(PROJECT, STAGE)
        self.assertNotEqual(first["review"]["id"], second["review"]["id"])

    # --------------------------------------------------------------- atomic
    def test_10_short_write_fails_closed(self):
        content = "# " + "A" * 500
        original = critique.os.write
        def short(fd, data):
            return original(fd, data[:len(data) // 2])
        with mock.patch.object(critique.os, "write", side_effect=short):
            with self.assertRaises(critique.CritiqueError):
                critique.immutable_artifact(PROJECT, "spec/short.md", content,
                                            {})

    def test_11_crash_before_workflow_version_recovers(self):
        rid = self._ready()
        text = self._good("build real artifact with evidence")
        text += "\n## Revision\nA real new section added by the producer.\n"
        critique.immutable_artifact(PROJECT, "spec/proposal-rev1.md", text,
                                    {"role": "producer"})
        out = critique.revise_artifact(PROJECT, STAGE, rid, [], "goal",
                                       self._producer(text))
        self.assertTrue(out["ok"])
        versions = workflow.by_project(PROJECT)["stages"][0]["versions"]
        self.assertEqual(len(versions), 2)
        self.assertEqual(len(critique.entries(PROJECT, "revision")), 1)

    def test_12_confirmation_replay_and_session_rejected(self):
        rid = self._ready()
        token = self._token(rid)
        out = critique.human_decision(PROJECT, STAGE, rid, "accept-current",
                                      token, self.session)
        self.assertTrue(out["ok"])
        with self.assertRaises(critique.CritiqueError):
            critique.human_decision(PROJECT, STAGE, rid, "accept-current",
                                    token, self.session)
        with self.assertRaises(critique.CritiqueError):
            critique.human_decision(PROJECT, STAGE, rid, "accept-current",
                                    "x", "bad-session")


if __name__ == "__main__":
    unittest.main()
