"""Phase 07 final acceptance suite — real producer, real idempotency, real HTTP."""
import http.cookiejar
import json
import multiprocessing
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import core.config as config
from core import confirmations, critique, panelauth, workflow
from core.tests import trusted_session

PROJECT = "p07-final"
STAGE = "s01-produce"
REL = "spec/proposal.md"


def _producer(project, stage, goal):
    content = ("# Producer output\n\n" + str(goal) +
               "\n\n## Evidence\nReal task-relevant content produced from the "
               "workflow goal and validated critic findings.\n")
    p = config.PROJECTS_DIR / project / REL
    p.write_text(content, encoding="utf-8")
    return {"status": "PASS", "artifacts": [
        {"path": REL, "sha256": workflow.sha256_file(REL, project)}]}


def _worker(project, stage, ledger, flags, conf, wfstore, queue):
    try:
        critique._LEDGER = Path(ledger)
        critique._FLAGS = Path(flags)
        confirmations._STORE = Path(conf)
        workflow._STORE = Path(wfstore)
        queue.put(("ok", critique.run_series(project, stage, _producer)))
    except Exception as exc:  # noqa: BLE001
        queue.put(("error", repr(exc)))


class FinalTest(unittest.TestCase):
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
        self._produce("x")
        self.root = root

    def _produce(self, text):
        (config.PROJECTS_DIR / PROJECT / REL).write_text(text, encoding="utf-8")
        out = workflow.run_stage(PROJECT, STAGE, "tok", self._executor())
        self.assertEqual(out.get("status"), "PASS", out)

    def _executor(self):
        def run(project, stage, goal):
            return {"status": "PASS", "artifacts": [
                {"path": REL, "sha256": workflow.sha256_file(REL, project)}]}
        return run

    def _set_content(self, text):
        (config.PROJECTS_DIR / PROJECT / REL).write_text(text, encoding="utf-8")
        sha = workflow.sha256_file(REL, PROJECT)
        with workflow._LOCK:
            items = workflow._load()
            rec = items[self._info()["workflow_id"]]
            st = next(x for x in rec["stages"] if x["id"] == STAGE)
            st["versions"][-1]["artifacts"][0]["sha256"] = sha
            workflow._save(items)
        return sha

    def _info(self):
        return critique._stage_artifact(PROJECT, STAGE)

    def _confirm(self, review_id, sha, option="accept-current", rejected=None,
                 evidence=None, cycle=1, session=None, fp=None):
        rejected = rejected if rejected is not None else [
            {"option": "revise", "reason": "human selected the reviewed option",
             "owner": "user"}]
        evidence = evidence if evidence is not None else [self._info()["path"]]
        s = session or self.session
        path = self._info()["path"]
        payload = {"project": PROJECT, "workflow_id": self._info()["workflow_id"],
                   "stage_id": STAGE, "review_id": review_id,
                   "artifact_path": path, "artifact_sha256": sha,
                   "option": option, "cycle": cycle,
                   "decision_fingerprint": fp or critique.decision_fingerprint(
                       PROJECT, self._info()["workflow_id"], STAGE, review_id,
                       path, sha, self._info()["version_no"], option, cycle, rejected, evidence,
                       panelauth.principal_for_session(s), s)}
        return confirmations.create(PROJECT, "critic-decision", payload,
                                     "approve?", s)["confirm_token"]

    def _ready(self):
        series = critique.run_series(PROJECT, STAGE, _producer)
        self.assertEqual(series["status"], "READY_FOR_HUMAN_DECISION", series)
        return series["final"]["review"]["id"]

    # ------------------------------------------------------- real producer
    def test_01_generic_template_never_passes(self):
        template = "TODO TBD Lorem ipsum placeholder sample text FIXME"
        self._set_content(template)
        review = critique.execute_review(PROJECT, STAGE)
        self.assertEqual(review["verdict"]["decision"], "reject")

    def test_02_real_producer_revision_is_task_relevant(self):
        review = critique.execute_review(PROJECT, STAGE)
        revised = critique.revise_artifact(PROJECT, STAGE,
                                           review["review"]["id"],
                                           review["findings"],
                                           "build real artifact with evidence",
                                           _producer)
        self.assertTrue(revised["ok"])
        text = (config.PROJECTS_DIR / PROJECT /
                revised["artifact"]["path"]).read_text(encoding="utf-8")
        self.assertIn("build real artifact", text)
        self.assertNotEqual(revised["artifact"]["sha256"],
                            self._info()["source_sha256"]
                            if "source_sha256" in self._info() else
                            review["artifact_sha256"])

    def test_03_producer_failure_is_hold_not_pass(self):
        def failing(project, stage, goal):
            return {"status": "FAIL", "error": "producer-unavailable"}
        out = critique.run_series(PROJECT, STAGE, failing)
        self.assertEqual(out["status"], "HOLD")
        self.assertIn("producer-failed", out.get("reason", ""))
        self.assertFalse(critique.entries(PROJECT, "decision"))

    def test_04_all_criteria_rechecked_on_new_version(self):
        review = critique.execute_review(PROJECT, STAGE)
        revised = critique.revise_artifact(PROJECT, STAGE,
                                           review["review"]["id"],
                                           review["findings"],
                                           "build real artifact with evidence",
                                           _producer)
        new_sha = revised["artifact"]["sha256"]
        workflow_stage = workflow.by_project(PROJECT)["stages"][0]
        version = workflow_stage["versions"][-1]
        self.assertEqual(version["artifacts"][0]["sha256"], new_sha)
        self.assertEqual(version["artifacts"][0]["path"],
                         revised["artifact"]["path"])
        after = critique.execute_review(PROJECT, STAGE)
        self.assertEqual(after["verdict"]["decision"], "pass")
        self.assertEqual(after["artifact_sha256"], new_sha)

    # -------------------------------------------------------- fingerprint
    def test_05_fingerprint_is_mandatory(self):
        rid = self._ready()
        with self.assertRaises(TypeError):
            critique.decide(PROJECT, "wf", STAGE, rid, REL,
                            self._info()["sha256"], "accept-current", [], [],
                            1, "x", self.session)

    def test_06_tampered_bound_fields_rejected(self):
        rid = self._ready()
        sha = self._info()["sha256"]
        base_rejected = [{"option": "revise", "reason": "r", "owner": "user"}]
        bad_cases = (
            {"option": "other-option"},
            {"rejected": [{"option": "revise", "reason": "tampered",
                           "owner": "user"}]},
            {"evidence": ["other/path"]},
            {"cycle": 9},
            {"sha": "b" * 64},
        )
        for case in bad_cases:
            with self.subTest(case=case):
                token = self._confirm(rid, case.get("sha", sha),
                                      case.get("option", "accept-current"),
                                      case.get("rejected", base_rejected),
                                      case.get("evidence", [REL]),
                                      case.get("cycle", 1))
                with self.assertRaises(critique.CritiqueError):
                    critique.human_decision(PROJECT, STAGE, rid,
                                            case.get("option", "accept-current"),
                                            token, self.session)

    def test_07_no_direct_approval_bypass(self):
        rid = self._ready()
        token = self._confirm(rid, self._info()["sha256"])
        critique.human_decision(PROJECT, STAGE, rid, "accept-current",
                                token, self.session)
        out = workflow.approve(PROJECT, STAGE, 1, "forged-approval")
        self.assertFalse(out.get("ok"))
        self.assertIn("governed-decision", out.get("error", ""))

    # ---------------------------------------------------------- idempotency
    def test_08_repeated_critique_has_no_duplicate_side_effects(self):
        def snapshot():
            return (len(critique.entries(PROJECT)),
                    [e["id"] for e in critique.entries(PROJECT)],
                    len(workflow.by_project(PROJECT)["stages"][0]["versions"]),
                    workflow.by_project(PROJECT)["rev"],
                    len(critique.entries(PROJECT, "verdict")))
        before = snapshot()
        self._ready()
        first = snapshot()
        self._ready()
        self._ready()
        after = snapshot()
        self.assertEqual(first, after)
        self.assertNotEqual(before, first)

    def test_09_thread_and_process_repeated_requests(self):
        valid = ("# Deliverable\n\n## Goal\nbuild real artifact with "
                 "evidence\n\n## Evidence\nReal content with inspectable "
                 "sections and verified SHA-256 for this deliverable.\n")
        self._set_content(valid)
        barrier = threading.Barrier(4)
        results = []
        def work():
            try:
                barrier.wait(timeout=10)
                results.append(critique.run_series(PROJECT, STAGE, _producer))
            except Exception as exc:  # noqa: BLE001
                results.append("ERR:" + repr(exc))
        threads = [threading.Thread(target=work) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(len(results), 4)
        self.assertEqual(len({r["final"]["review"]["id"]
                              for r in results if isinstance(r, dict)}), 1,
                 str(results)[:500])
        self.assertEqual(len(critique.entries(PROJECT, "verdict")), 1)

        q = multiprocessing.Queue()
        root = self.root
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
        self.assertEqual(len(critique.entries(PROJECT, "verdict")), 1)

    def test_10_recovery_has_no_duplicate_transitions(self):
        rid = self._ready()
        token = self._confirm(rid, self._info()["sha256"])
        with mock.patch.object(workflow, "approve",
                               return_value={"ok": False, "error": "injected"}):
            out = critique.human_decision(PROJECT, STAGE, rid,
                                          "accept-current", token, self.session)
        self.assertFalse(out["ok"])
        before = (len(critique.entries(PROJECT)),
                  workflow.by_project(PROJECT)["rev"])
        recovered = critique.recover_decision(PROJECT, STAGE)
        self.assertTrue(recovered["ok"], str(recovered))
        after = (len(critique.entries(PROJECT)),
                 workflow.by_project(PROJECT)["rev"])
        self.assertEqual(workflow.by_project(PROJECT)["status"], "COMPLETED")
        self.assertEqual(len(critique.entries(PROJECT, "decision")), 1)
        # second recovery is a no-op, never a second transition
        second = critique.recover_decision(PROJECT, STAGE)
        self.assertFalse(second["ok"])
        self.assertEqual(after, (len(critique.entries(PROJECT)),
                                 workflow.by_project(PROJECT)["rev"]))

    # ---------------------------------------------------------------- HTTP
    def test_11_real_http_auth_csrf_critique_and_decision(self):
        import core.server as server
        rid = self._ready()
        token = self._confirm(rid, self._info()["sha256"])
        password = Path.home().joinpath(".af-panel/panel-pass.txt").read_text(
            encoding="utf-8").strip()
        with mock.patch.object(config, "PORT", 0):
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.shutdown)
        self.addCleanup(thread.join, 5)
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar))
        base = "http://127.0.0.1:%d" % port
        def post(route, payload, csrf=""):
            req = urllib.request.Request(
                base + route, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json",
                         "X-Panel-CSRF": csrf}, method="POST")
            return json.loads(opener.open(req, timeout=10).read())
        def get(route):
            return json.loads(opener.open(base + route, timeout=10).read())
        from core.tests import _TEST_PASSWORD
        state = get("/api/panel/state")
        # Additional known test-fixture password used by the auth suite.
        passwords = [password, _TEST_PASSWORD, "AF26-Strong-Passphrase-123"]
        if not state.get("initialized"):
            post("/api/panel/init", {"password": passwords[0]})
        logged_in = False
        for candidate in passwords:
            try:
                post("/api/panel/login", {"password": candidate})
                logged_in = True
                break
            except urllib.error.HTTPError:
                continue
        self.assertTrue(logged_in, "panel login rejected all test credentials")
        csrf = next(c.value for c in jar if c.name == panelauth.CSRF_COOKIE)
        http_session = next(c.value for c in jar
                            if c.name == panelauth.SESSION_COOKIE)
        # unauthenticated mutation must fail at the real socket
        raw = urllib.request.Request(
            base + "/api/workflow/critique/run", data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(raw, timeout=10)
        got = get("/api/workflow/critique?project=" + PROJECT)
        self.assertTrue(got["ok"])
        got = get("/api/workflow/critique?project=" + PROJECT)
        live_review = next(e["id"] for e in reversed(got["entries"])
                           if e.get("kind") == "review")
        live_sha = next(e["artifact_sha256"] for e in reversed(got["entries"])
                        if e.get("kind") == "review")
        live_token = self._confirm(live_review, live_sha,
                                   session=http_session)
        try:
            decided = post("/api/workflow/critique/decide", {
                "project": PROJECT, "stage": STAGE, "review_id": live_review,
                "option": "accept-current", "confirm_token": live_token}, csrf)
        except urllib.error.HTTPError as exc:
            raise AssertionError("HTTP decide failed: " +
                                 exc.read().decode()[:300]) from exc
        self.assertTrue(decided["ok"], str(decided))
        self.assertEqual(workflow.by_project(PROJECT)["stages"][0]["status"],
                         "APPROVED")


if __name__ == "__main__":
    unittest.main()
