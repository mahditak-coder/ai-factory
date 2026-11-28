"""Phase 07 corrective regressions — real content, verdict binding, security."""
import json
import multiprocessing
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import confirmations, critique, panelauth, workflow
from core.tests import trusted_session

PROJECT = "p07-fix"
STAGE = "s01-produce"


def _worker(project, stage, ledger, flags, conf, wfstore, queue):
    try:
        critique._LEDGER = Path(ledger)
        critique._FLAGS = Path(flags)
        confirmations._STORE = Path(conf)
        workflow._STORE = Path(wfstore)
        queue.put(("ok", critique.run_series(project, stage)))
    except Exception as exc:  # noqa: BLE001
        queue.put(("error", repr(exc)))


class Gate:
    def __init__(self, path, command, session, csrf_header="", csrf_cookie=""):
        self.path = path
        self.command = command
        self.headers = {"X-Panel-CSRF": csrf_header}
        self._session = session
        self._csrf_cookie = csrf_cookie
        self.answers = []

    def _cookie(self, name):
        if name == panelauth.SESSION_COOKIE:
            return self._session
        if name == panelauth.CSRF_COOKIE:
            return self._csrf_cookie
        return ""

    def _error(self, code, message):
        self.answers.append((code, message))


class CorrectiveTest(unittest.TestCase):
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
        workflow.create(PROJECT, "webapp", "real deliverable with verified content",
                        dag=[{"id": STAGE, "role": "developer", "title": "p",
                              "kind": "code", "deps": []}])
        self.path = "spec/proposal.md"
        (d / self.path).write_text(
            "# Proposal\n\n## Goal\nBuild a real deliverable with evidence and "
            "verifiable acceptance criteria.\n\n## Evidence\nSHA-256 verified.\n",
            encoding="utf-8")
        workflow.run_stage(PROJECT, STAGE, "tok", self._executor())

    def _executor(self):
        def run(project, stage, goal):
            sha = workflow.sha256_file(self.path, project)
            p = config.PROJECTS_DIR / project / self.path
            return {"status": "PASS", "artifacts": [
                {"path": self.path, "sha256": sha, "size": p.stat().st_size}]}
        return run

    def _set_content(self, text):
        (config.PROJECTS_DIR / PROJECT / self.path).write_text(
            text, encoding="utf-8")
        sha = workflow.sha256_file(self.path, PROJECT)
        with workflow._LOCK:
            items = workflow._load()
            rec = items.get(self._info()["workflow_id"])
            st = next(s for s in rec["stages"] if s["id"] == STAGE)
            st["versions"][-1]["artifacts"][0]["sha256"] = sha
            workflow._save(items)
        return sha

    def _info(self):
        return critique._stage_artifact(PROJECT, STAGE)

    def _series(self):
        return critique.run_series(PROJECT, STAGE)

    def _verdict(self):
        s = self._series()
        return s["final"]["review"]["id"], s

    def _confirm(self, review_id, sha, option="accept-current", cycle=1,
                 session=None, rejected=None, evidence=None):
        rejected = rejected if rejected is not None else [
            {"option": "revise", "reason": "human selected the reviewed option",
             "owner": "user"}]
        evidence = evidence or [self.path]
        return confirmations.create(PROJECT, "critic-decision", {
            "project": PROJECT, "workflow_id": self._info()["workflow_id"],
            "stage_id": STAGE, "review_id": review_id, "artifact_path": self.path,
            "artifact_sha256": sha, "option": option, "cycle": cycle,
            "decision_fingerprint": critique.decision_fingerprint(
                PROJECT, self._info()["workflow_id"], STAGE, review_id, sha,
                option, cycle, rejected, evidence)}, "q", session or self.session
        )["confirm_token"]

    # ---------------------------------------------------------- content truth
    def test_01_empty_artifact_never_passes(self):
        self._set_content("")
        review = critique.execute_review(PROJECT, STAGE)
        self.assertEqual(review["verdict"]["decision"], "reject")
        self.assertTrue(any(f["criterion"] in ("non-empty", "meaningful-content")
                            for f in review["findings"]))

    def test_02_html_comment_only_is_not_content(self):
        self._set_content("<!-- a very long comment with plenty of words that "
                          "is not real deliverable content at all -->")
        review = critique.execute_review(PROJECT, STAGE)
        self.assertEqual(review["verdict"]["decision"], "reject")
        self.assertTrue(all(c["score"] == 0 for c in review["checks"]
                            if c["criterion"] == "non-empty"))

    def test_03_placeholder_text_rejected(self):
        self._set_content("TODO placeholder TBD Lorem ipsum FIXME sample text " * 6)
        review = critique.execute_review(PROJECT, STAGE)
        self.assertEqual(review["verdict"]["decision"], "reject")
        self.assertTrue(any(c["criterion"] == "meaningful-content" and
                            c["score"] == 0 for c in review["checks"]))

    def test_04_every_criterion_evaluated_separately(self):
        review = critique.execute_review(PROJECT, STAGE)
        ids = {c["criterion"] for c in review["checks"]}
        self.assertEqual(ids, {"artifact-exists", "sha256-verified", "non-empty",
                               "meaningful-content", "has-sections"})
        self.assertTrue(all(isinstance(c["score"], int) for c in review["checks"]))

    def test_05_changed_sha_stops_review(self):
        (config.PROJECTS_DIR / PROJECT / self.path).write_text(
            "# tampered\n\n## Goal\nCompletely different real content here.\n",
            encoding="utf-8")
        with self.assertRaises(critique.CritiqueError) as ctx:
            critique.execute_review(PROJECT, STAGE)
        self.assertIn("hash", str(ctx.exception))

    # --------------------------------------------------------------- findings
    def test_06_forged_finding_blocked_in_record_verdict(self):
        info = self._info()
        rv = critique.start_review(PROJECT, info["workflow_id"], STAGE,
                                   "developer", self.path, info["sha256"],
                                   ["non-empty"], artifact_version=1)
        revoked = {"id": "f-forged", "review_id": rv["id"],
                   "severity": "critical", "criterion": "non-empty",
                   "version": 1, "description": "fake", "evidence_ref": "x",
                   "evidence": {}, "artifact_sha256": info["sha256"],
                   "required_correction": "x", "owner": "x", "status": "open",
                   "created_at": "now", "history": [{"status": "open"}]}
        with self.assertRaises(critique.CritiqueError) as ctx:
            critique.record_verdict(rv["id"], rv["critic_run_token"],
                                    [revoked], {"schema": critique.RUBRIC_SCHEMA})
        self.assertIn("finding-validation-failed", str(ctx.exception))

    def test_07_finding_from_other_review_rejected(self):
        review = critique.execute_review(PROJECT, STAGE)
        f = critique.new_finding("another-review", "high", "x", self.path, 1,
                                 review["artifact_sha256"], "fix", "critic",
                                 criterion="non-empty",
                                 evidence={"path": self.path,
                                           "sha256": review["artifact_sha256"]})
        result = critique.validate_findings(
            PROJECT, [f], self.path, review["artifact_sha256"],
            ["non-empty"], review["review"]["id"], 1)
        self.assertFalse(result["ok"])

    # --------------------------------------------------------------- revision
    def test_08_revision_really_changes_content_and_keeps_old(self):
        old_sha = self._set_content("")
        review = critique.execute_review(PROJECT, STAGE)

        revised = critique.revise_artifact(PROJECT, STAGE,
                                           review["review"]["id"],
                                           review["findings"],
                                           "real deliverable")
        self.assertTrue(revised["ok"])
        new_content = (config.PROJECTS_DIR / PROJECT /
                       revised["artifact"]["path"]).read_text(encoding="utf-8")
        self.assertGreater(len(new_content.strip()), 40)
        self.assertEqual(critique.artifact_hash(PROJECT, self.path), old_sha)
        self.assertNotEqual(revised["artifact"]["sha256"], old_sha)

    def test_09_placeholder_revision_fails_closed(self):
        finding = {"criterion": "unknown-criterion", "id": "f-x"}
        with self.assertRaises(critique.CritiqueError) as ctx:
            critique.revise_artifact(PROJECT, STAGE, "r", [finding], "goal")
        self.assertIn("correction-not-applicable", str(ctx.exception))

    def test_10_old_verdict_cannot_approve_new_version(self):
        self._set_content("")
        first = critique.execute_review(PROJECT, STAGE)
        critique.revise_artifact(PROJECT, STAGE, first["review"]["id"],
                                 first["findings"], "goal")
        with self.assertRaises(critique.CritiqueError) as ctx:
            critique.human_decision(PROJECT, STAGE, first["review"]["id"],
                                    "accept-current", "x", self.session)
        self.assertIn("valid-verdict", str(ctx.exception))

    # ------------------------------------------------------------- human gate
    def test_11_no_verdict_means_no_approval(self):
        with self.assertRaises(critique.CritiqueError):
            critique.human_decision(PROJECT, STAGE, "rv-missing",
                                    "accept-current", "x", self.session)

    def test_12_hold_cannot_be_approved(self):
        rid, _ = self._verdict()
        critique._append({"id": "hold-x", "kind": "hold", "project": PROJECT,
                          "stage_id": STAGE, "status": "HOLD",
                          "review_id": rid, "series_key":
                          critique._series_key(PROJECT, STAGE)})
        workflow.reject(PROJECT, STAGE, "hold")
        token = self._confirm(rid, self._info()["sha256"])
        with self.assertRaises(critique.CritiqueError):
            critique.human_decision(PROJECT, STAGE, rid, "accept-current",
                                    token, self.session)

    def test_13_forged_and_replayed_confirmation_rejected(self):
        rid, _ = self._verdict()
        with self.assertRaises(critique.CritiqueError):
            critique.human_decision(PROJECT, STAGE, rid, "accept-current",
                                    "forged", self.session)
        token = self._confirm(rid, self._info()["sha256"])
        out = critique.human_decision(PROJECT, STAGE, rid, "accept-current",
                                      token, self.session)
        self.assertTrue(out["ok"])
        with self.assertRaises(critique.CritiqueError):
            critique.human_decision(PROJECT, STAGE, rid, "accept-current",
                                    token, self.session)

    def test_14_invalid_session_and_option_rejected(self):
        rid, _ = self._verdict()
        sha = self._info()["sha256"]
        with self.assertRaises(critique.CritiqueError):
            critique.human_decision(PROJECT, STAGE, rid, "accept-current",
                                    "x", "bad-session")
        token = self._confirm(rid, sha, option="bogus-option")
        with self.assertRaises(critique.CritiqueError):
            critique.human_decision(PROJECT, STAGE, rid, "bogus-option",
                                    token, self.session)

    def test_15_approve_failure_never_reports_success(self):
        rid, _ = self._verdict()
        token = self._confirm(rid, self._info()["sha256"])
        with mock.patch.object(workflow, "approve",
                               return_value={"status": "FAIL", "ok": False,
                                             "error": "injected"}):
            out = critique.human_decision(PROJECT, STAGE, rid,
                                          "accept-current", token, self.session)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "RECOVERABLE")
        self.assertTrue(critique.entries(PROJECT, "decision-pending"))

    def test_16_recovery_after_mid_failure(self):
        rid, _ = self._verdict()
        token = self._confirm(rid, self._info()["sha256"])
        with mock.patch.object(workflow, "approve",
                               return_value={"status": "FAIL", "ok": False,
                                             "error": "injected"}):
            critique.human_decision(PROJECT, STAGE, rid, "accept-current",
                                    token, self.session)
        recovered = critique.recover_decision(PROJECT, STAGE)
        self.assertTrue(recovered["ok"])
        self.assertEqual(workflow.by_project(PROJECT)["stages"][0]["status"],
                         "APPROVED")

    # ----------------------------------------------------------- cycles/races
    def test_17_concurrent_series_one_cycle_winner(self):
        barrier = threading.Barrier(4)
        results = []
        def work():
            try:
                barrier.wait(timeout=10)
                results.append(critique.run_series(PROJECT, STAGE))
            except Exception as exc:  # noqa: BLE001
                results.append("ERR:" + repr(exc))
        threads = [threading.Thread(target=work) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(len(results), 4)
        self.assertTrue(all(isinstance(r, dict) and r.get("status") ==
                            "READY_FOR_HUMAN_DECISION" for r in results), results)
        self.assertEqual(critique._series_cycle(PROJECT, STAGE), 0)

    def test_18_process_race_and_fourth_cycle_blocked(self):
        q = multiprocessing.Queue()
        root = Path(self.tmp.name)
        procs = [multiprocessing.Process(target=_worker, args=(
            PROJECT, STAGE, str(root / "ledger.json"), str(root / "flags.json"),
            str(root / "confirm.json"), str(root / "workflow.json"), q))
            for _ in range(3)]
        for p in procs:
            p.start()
        replies = [q.get(timeout=60) for _ in procs]
        for p in procs:
            p.join(timeout=60)
        self.assertTrue(all(kind == "ok" for kind, _ in replies), replies)
        with mock.patch.object(critique, "critic_enabled", return_value=True):
            for n in range(1, 4):
                critique._append({"id": "rev-%d-%s" %
                                  (n, critique._series_key(PROJECT, STAGE)),
                                  "kind": "revision", "project": PROJECT,
                                  "stage_id": STAGE, "series_key":
                                  critique._series_key(PROJECT, STAGE)})
            out = critique.run_series(PROJECT, STAGE)
        self.assertEqual(out["status"], "HOLD")
        versions = workflow.by_project(PROJECT)["stages"][0]["versions"]
        self.assertEqual(len(versions), 1)

    def test_19_repeated_review_is_idempotent_on_same_version(self):
        first = critique.execute_review(PROJECT, STAGE)
        second = critique.execute_review(PROJECT, STAGE)
        self.assertEqual(first["verdict"]["decision"],
                         second["verdict"]["decision"])
        self.assertEqual(first["artifact_sha256"], second["artifact_sha256"])

    def test_20_restart_keeps_lineage(self):
        first = critique.execute_review(PROJECT, STAGE)
        before = critique.entries(PROJECT)
        workflow._ACTIVE.clear()
        after = critique.entries(PROJECT)
        self.assertEqual(len(before), len(after))
        self.assertTrue(all("seq" in e and "at" in e for e in after))

    # -------------------------------------------------------------- API gates
    def test_21_api_requires_session_and_csrf(self):
        from core import server
        csrf = panelauth.csrf_of(self.session)
        denied = Gate("/api/workflow/critique/run", "POST", "")
        self.assertFalse(server.Handler._panel_gate(denied))
        self.assertEqual(denied.answers[-1][0], 401)
        bad = Gate("/api/workflow/critique/run", "POST", self.session,
                   "wrong", "wrong")
        self.assertFalse(server.Handler._panel_gate(bad))
        self.assertEqual(bad.answers[-1][0], 403)
        good = Gate("/api/workflow/critique/run", "POST", self.session,
                    csrf, csrf)
        self.assertTrue(server.Handler._panel_gate(good))

    def test_22_api_uses_same_decision_logic(self):
        from core import server
        rid, _ = self._verdict()
        token = self._confirm(rid, self._info()["sha256"])
        class H:
            def __init__(self, body, session):
                self.body, self.session, self.out = body, session, []
            def _read_json(self): return self.body
            def _cookie(self, name): return self.session if name == \
                panelauth.SESSION_COOKIE else ""
            def _json(self, p): self.out.append((200, p))
            def _error(self, c, m): self.out.append((c, m))
        good = H({"project": PROJECT, "stage": STAGE, "review_id": rid,
                  "option": "accept-current", "confirm_token": token},
                 self.session)
        server.Handler._api_workflow_critique_decide(good)
        self.assertEqual(good.out[-1][0], 200)
        self.assertEqual(workflow.by_project(PROJECT)["stages"][0]["status"],
                         "APPROVED")

    def test_23_no_bypass_without_human_decision(self):
        rid, _ = self._verdict()
        self.assertEqual(workflow.by_project(PROJECT)["stages"][0]["status"],
                         "AWAITING_USER_REVIEW")
        self.assertTrue(critique.entries(PROJECT, "verdict"))
        self.assertFalse(critique.entries(PROJECT, "decision"))


if __name__ == "__main__":
    unittest.main()
