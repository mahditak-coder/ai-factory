"""AF-36 — browser-independent events, ASK/resume and governance tests.

Real functions on an isolated store. Mocked OpenCode responses are UNIT tests
only; the real model/tool E2E is reported separately.
"""
import json
import time
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import (action_gate, agent_events, confirmations, opencode_bridge,
                  tasks)


def _fixture(name):
    d = config.PROJECTS_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps(
        {"name": name, "type": "webpage", "version": "1.0.0"}), encoding="utf-8")
    (d / "index.html").write_text("<html></html>", encoding="utf-8")
    return d


class BackendRecordingTest(unittest.TestCase):
    def test_tool_events_recorded_without_browser_and_no_duplicates(self):
        p = "af36-record"
        _fixture(p)
        raw = {"data": [{
            "type": "assistant", "agent": "builder", "finish": "tool-calls",
            "content": [{
                "type": "tool", "id": "call-af36", "name": "shell",
                "state": {"status": "completed",
                          "input": {"command": "python tests/test.py"},
                          "content": [{"type": "text", "text": "PASS"}],
                          "metadata": {"exit": 0}}},
            ]}]}
        with mock.patch.object(opencode_bridge, "_request", return_value=raw):
            n1 = opencode_bridge.record_tool_events(p, "ses_af36", "t-af36")
            n2 = opencode_bridge.record_tool_events(p, "ses_af36", "t-af36")
        ev = [e for e in agent_events.events(project=p)
              if e.get("call_id") == "call-af36"]
        self.assertEqual(n1, 1)
        self.assertEqual(len(ev), 1, ev)
        self.assertEqual(ev[0]["status"], "PASS")
        self.assertEqual(ev[0]["task_id"], "t-af36")
        self.assertEqual(ev[0]["session_id"], "ses_af36")

    def test_failed_tool_from_backend_is_not_success(self):
        p = "af36-fail"
        _fixture(p)
        raw = {"data": [{"type": "assistant", "content": [{
            "type": "tool", "id": "call-fail", "name": "shell",
            "state": {"status": "completed", "input": {},
                      "content": [{"type": "text", "text": "FAIL"}],
                      "metadata": {"exit": 2}}}]}]}
        with mock.patch.object(opencode_bridge, "_request", return_value=raw):
            opencode_bridge.record_tool_events(p, "ses_fail")
        ev = [e for e in agent_events.events(project=p)
              if e.get("call_id") == "call-fail"]
        self.assertEqual(ev[0]["status"], "FAIL", ev[0])


class AskResumeTest(unittest.TestCase):
    def test_one_shot_token_resumes_same_task_and_old_token_fails(self):
        p = "af36-ask"
        _fixture(p)
        t = tasks.add_task(p, "deploy the product", max_attempts=2)
        tasks.claim_next("w1")
        cf = confirmations.create(p, "agent-ask",
                                  {"task_id": t["id"], "question": "ادامه؟"},
                                  "ادامه؟", "")
        tasks.ask_task(t["id"], "ادامه؟", ["بله", "خیر"], cf["confirm_token"],
                       "CONFIRM")
        self.assertIsNone(tasks.claim_next("w2"))
        self.assertEqual(tasks.get_task(t["id"])["state"], "awaiting_user")
        with self.assertRaises(tasks.TaskError):
            tasks.answer_task(t["id"], cf["confirm_token"] + "bad", "بله")
        resumed = tasks.answer_task(t["id"], cf["confirm_token"], "بله")
        self.assertEqual(resumed["state"], "queued")
        self.assertEqual(resumed["ask_answer"], "بله")
        with self.assertRaises(tasks.TaskError):
            tasks.answer_task(t["id"], cf["confirm_token"], "بله")

    def test_expired_token_cannot_resume(self):
        p = "af36-expire"
        _fixture(p)
        t = tasks.add_task(p, "go", max_attempts=2)
        cf = confirmations.create(p, "agent-ask", {}, "سؤال؟", "")
        tasks.ask_task(t["id"], "سؤال؟", [], cf["confirm_token"], "ASK")
        store = confirmations._load()
        store["items"][cf["confirm_token"]]["expires"] = 1
        confirmations._save(store)
        with self.assertRaises(tasks.TaskError):
            tasks.answer_task(t["id"], cf["confirm_token"], "بله")
        self.assertEqual(tasks.get_task(t["id"])["state"], "awaiting_user")

    def test_human_view_redacts_token(self):
        p = "af36-redact"
        _fixture(p)
        t = tasks.add_task(p, "go", max_attempts=2)
        tasks.ask_task(t["id"], "سؤال؟", [], "secret-token", "ASK")
        view = tasks._iso_view(tasks.get_task(t["id"]))
        self.assertNotIn("token", view.get("ask", {}))


class GateGovernanceTest(unittest.TestCase):
    def test_deploy_gate_requires_real_health(self):
        self.assertEqual(action_gate.gate_deploy(
            {"ok": True, "http": 200, "path": "/api/health"})["status"], "PASS")
        self.assertNotEqual(action_gate.gate_deploy(
            {"ok": False, "http": None})["status"], "PASS")

    def test_permission_gate_states(self):
        self.assertEqual(action_gate.gate_permissions("once")["status"], "PASS")
        self.assertEqual(action_gate.gate_permissions("reject")["status"], "BLOCKED")
        self.assertEqual(action_gate.gate_permissions(None)["status"], "NOT TESTED")

    def test_version_is_single_source_of_truth(self):
        self.assertEqual(config.APP_VERSION, "0.47.0")


if __name__ == "__main__":
    unittest.main()
