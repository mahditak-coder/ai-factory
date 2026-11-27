"""AF-35 — integrity regression tests for confirmed source defects.

Real functions on an isolated store. These are UNIT/INTEGRATION tests; the
real model/tool E2E is reported separately and is not substituted here.
"""
import json
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import (action_gate, agent_events, agent_planner, boundary, opstatus,
                  orchestrator, project_git, smart_release as sr, tasks)


def _fixture(name):
    d = config.PROJECTS_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps(
        {"name": name, "type": "webpage", "version": "1.0.0"}), encoding="utf-8")
    (d / "index.html").write_text("<html></html>", encoding="utf-8")
    return d


class ToolEventIntegrityTest(unittest.TestCase):
    def test_tool_failed_event_is_recorded_as_real_failure(self):
        p = "af35-toolfail"
        _fixture(p)
        called = {"id": "call-fail"}
        agent_events.record({"id": "e1", "type": "session.tool.called", "data": {
            "sessionID": "ses_x", "id": called["id"], "name": "shell",
            "input": {"command": "python bad.py"}}}, p, "ses_x")
        agent_events.record({"id": "e2", "type": "session.tool.failed", "data": {
            "sessionID": "ses_x", "id": called["id"], "error": "real failure",
            "metadata": {"exit": "1"}}}, p, "ses_x")
        ev = [e for e in agent_events.events(project=p)
              if e.get("call_id") == called["id"]]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["status"], opstatus.FAIL, ev[0])
        self.assertIn("real failure", ev[0]["error"])
        self.assertNotIn("نتیجهای ثبت نشد", ev[0]["detail"])

    def test_tool_success_with_nonzero_metadata_exit_is_failure(self):
        p = "af35-exit"
        _fixture(p)
        e = agent_events.record({"id": "e3", "type": "session.tool.success", "data": {
            "sessionID": "ses_x", "id": "call-exit", "name": "shell",
            "content": [], "metadata": {"exit": "2"}}}, p, "ses_x")
        self.assertEqual(e["status"], opstatus.FAIL, e)

    def test_permission_decision_is_deduplicated(self):
        p = "af35-permdup"
        _fixture(p)
        agent_events.record_permission_decision("ses_x", "req-1", "once", request={
            "resources": ["x.py"]})
        agent_events.record_permission_decision("ses_x", "req-1", "once", request={
            "resources": ["x.py"]})
        items = [e for e in agent_events.events(session="ses_x")
                 if e.get("call_id") == "req-1"]
        self.assertEqual(len(items), 1)


class GateIntegrityTest(unittest.TestCase):
    def test_no_coverage_is_not_tested_not_fail(self):
        s = action_gate.gate_test({"ran": False, "ok": False, "note": "no coverage"})
        self.assertEqual(s["status"], opstatus.NOT_TESTED, s)

    def test_idle_success_does_not_hide_assistant_completion(self):
        msgs = [{"type": "idle", "outcome": "succeeded"},
                {"type": "assistant", "finish": "stop", "completed": 1}]
        self.assertEqual(action_gate.turn_verdict(msgs), opstatus.PASS)

    def test_multi_resource_permission_requires_all_in_project(self):
        p = "af35-perm"
        _fixture(p)
        self.assertEqual(boundary.headless_permission(
            "edit", ["safe.py", "../config.py"], p), "reject")
        self.assertEqual(boundary.headless_permission(
            "edit", ["a.py", "b.py"], p), "once")
        self.assertEqual(boundary.headless_permission(
            "shell", 'python3 -c "import shutil; shutil.rmtree(1)"', p), "reject")


class TaskStateIntegrityTest(unittest.TestCase):
    def test_cancel_is_not_overwritten_by_late_finish_or_fail(self):
        _fixture("af35-cancel")
        t = tasks.add_task("af35-cancel", "fix title", max_attempts=2)
        tasks.claim_next("w1")
        tasks.cancel_task(t["id"])
        tasks.finish_task(t["id"], {"ran": True, "ok": True})
        self.assertEqual(tasks.get_task(t["id"])["state"], "cancelled")
        tasks.fail_task(t["id"], "late failure")
        self.assertEqual(tasks.get_task(t["id"])["state"], "cancelled")

    def test_done_task_is_not_reverted_by_late_worker_exception(self):
        _fixture("af35-done")
        t = tasks.add_task("af35-done", "fix title", max_attempts=2)
        tasks.claim_next("w1")
        tasks.finish_task(t["id"], {"ok": True})
        tasks.fail_task(t["id"], "telemetry crash")
        self.assertEqual(tasks.get_task(t["id"])["state"], "done")


class PlannerIntegrityTest(unittest.TestCase):
    def test_skip_is_not_green_and_replan_requires_failed_evidence(self):
        p = "af35-plan"
        _fixture(p)
        plan = agent_planner.create(p, "fix title")
        agent_planner.mark(plan["id"], "replan_me", "SKIP", "") if any(
            s["id"] == "replan_me" for s in plan["steps"]) else None
        # SKIP must never unlock the next step.
        agent_planner.mark(plan["id"], "inspect", opstatus.SKIP, "skipped")
        self.assertNotEqual(plan["status"], "PASS")
        self.assertNotEqual((agent_planner.next_step(plan) or {}).get("id"), "implement")
        self.assertIsNone(agent_planner.replan(plan["id"], "", "verify"))
        agent_planner.mark(plan["id"], "verify", opstatus.FAIL, "red")
        changed = agent_planner.replan(plan["id"], "real error", "verify",
                                       strategy="alternate-tool")
        self.assertIsNotNone(changed)
        self.assertEqual(changed["steps"][next(i for i, s in enumerate(changed["steps"])
                                               if s["id"] == "verify")]["strategy"],
                         "alternate-tool")


class SmartReleaseGateTest(unittest.TestCase):
    def test_unverified_tests_do_not_pass_git_preconditions(self):
        p = "af35-sr"
        _fixture(p)
        with mock.patch.object(project_git, "ensure_ready", return_value={
                "status": "ok", "connected": True, "created": False,
                "note": "mocked connected repo"}), \
             mock.patch.object(sr.factory, "testrun_combined",
                               return_value={"ran": False, "ok": False,
                                             "note": "no coverage"}), \
             mock.patch.object(sr.git_sync, "commit_and_push") as push:
            r = sr.run(p, downloads_dir=str(config.LOGS_DIR / "af35-dl"),
                       push=True, auto_git=True)
        self.assertEqual(push.call_count, 0)
        self.assertEqual(r["git_preconditions"]["status"], opstatus.NOT_TESTED)


if __name__ == "__main__":
    unittest.main()
