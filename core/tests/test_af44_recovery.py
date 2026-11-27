"""AF-44 — non-terminal policy rejection & same-session recovery (UNIT).

These are unit/integration tests on REAL functions with mocked transport.
They are NOT E2E proof of the live model; the live E2E is reported separately.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
import core.opencode_bridge as ob
from core import (agent_events, agent_loop, boundary, opstatus,
                  project_snapshot)


def _fixture(name: str, node: bool = False) -> Path:
    d = config.PROJECTS_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps({"name": name, "type": "webpage"}),
                                 encoding="utf-8")
    (d / "app.py").write_text("def compute(x): return x + 1\n", encoding="utf-8")
    (d / "tests").mkdir(exist_ok=True)
    (d / "tests/test_a.py").write_text(
        "from app import compute\ndef test_a(): assert compute(1)==2\ntest_a()\n",
        encoding="utf-8")
    if node:
        (d / "package.json").write_text('{"scripts":{"test":"node --test"}}',
                                        encoding="utf-8")
    return d


class BoundaryExplanationTest(unittest.TestCase):
    def test_network_rejection_is_structured_not_success(self):
        p = "af44-exp-net"
        _fixture(p)
        exp = boundary.explain_rejection("shell", ["curl http://evil.invalid"], p)
        self.assertEqual(exp["decision"], "reject")
        self.assertEqual(exp["reason"], "network-forbidden")
        self.assertIn("curl http://evil.invalid", exp["forbidden"][0])
        self.assertIn(str(project_snapshot.paths.project_dir(p).resolve()),
                      exp["scope"])
        self.assertIn("POLICY BLOCK", exp["feedback"])
        self.assertIn("reason=network-forbidden", exp["feedback"])
        self.assertIn("do NOT repeat", exp["feedback"])
        self.assertEqual(len(exp["signature"]), 16)
        self.assertEqual(exp["signature"], boundary.explain_rejection(
            "shell", ["curl http://evil.invalid"], p)["signature"])

    def test_outside_path_and_destructive_are_explained(self):
        p = "af44-exp-path"
        _fixture(p)
        outside = str(Path(config.PROJECT_ROOT) / "core" / "factory.py")
        a = boundary.explain_rejection("shell", ["grep -n x " + outside], p)
        b = boundary.explain_rejection("shell", ["rm -rf /sdcard"], p)
        c = boundary.explain_rejection("edit", ["../../etc/passwd"], p)
        self.assertEqual(a["reason"], "outside-project-path")
        self.assertEqual(b["reason"], "destructive-command")
        self.assertEqual(c["reason"], "outside-project-path")
        for exp in (a, b, c):
            self.assertEqual(boundary.headless_permission(
                "shell" if exp is b else ("edit" if exp is c else "shell"),
                exp["forbidden"], p), "reject")

    def test_allowed_command_keeps_explanation_but_decision_once(self):
        p = "af44-exp-ok"
        _fixture(p)
        self.assertEqual(boundary.headless_permission(
            "shell", ["python tests/test_a.py"], p), "once")


class PermissionReplyTest(unittest.TestCase):
    def test_reply_sends_message_and_validates_decision(self):
        with mock.patch.object(ob, "ensure_running"), \
             mock.patch.object(ob, "_request") as req:
            ob.permission_reply("ses_x", "per_x", "reject",
                                message="POLICY BLOCK: forbidden path")
            body = req.call_args.kwargs["body"]
            self.assertEqual(body["decision"], "reject")
            self.assertEqual(body["message"], "POLICY BLOCK: forbidden path")
            ob.permission_reply("ses_x", "per_y", "once")
            self.assertNotIn("message", req.call_args.kwargs["body"])
            with self.assertRaises(ob.OcError):
                ob.permission_reply("ses_x", "per_z", "deny")


class PolicyRejectionEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.write(b"[]")
        self.tmp.close()
        self.store = Path(self.tmp.name)
        self.patcher = mock.patch.object(agent_events, "_STORE", self.store)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.store.unlink(missing_ok=True)

    def test_rejection_is_blocked_evidence_and_feeds_next_decision(self):
        project, sid, tid = "af44-ev", "ses_ev", "t-ev"
        _fixture(project)
        exp = boundary.explain_rejection("shell", ["curl http://evil.invalid"],
                                         project)
        agent_events.record_policy_rejection(sid, "per_ev", project=project,
                                             task_id=tid, action="shell",
                                             resource=["curl http://evil.invalid"],
                                             explanation=exp)
        # idempotent: same request id must not double-count
        agent_events.record_policy_rejection(sid, "per_ev", project=project,
                                             task_id=tid, action="shell",
                                             resource=["curl http://evil.invalid"],
                                             explanation=exp)
        ev = [e for e in agent_events.events(project=project)
              if e.get("kind") == "policy.rejection"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["status"], opstatus.BLOCKED)
        self.assertNotEqual(ev[0]["status"], opstatus.PASS)
        prompt = agent_events.decision_prompt(project, sid, tid)
        self.assertIn("network-forbidden", prompt)
        self.assertIn("curl http://evil.invalid", prompt)
        self.assertIn("تکرار نکن", prompt)
        st = agent_events.policy_rejection_stats(project, sid, tid)
        self.assertEqual(st["total"], 1)
        self.assertEqual(st["distinct"], 1)
        self.assertEqual(st["signatures"], [exp["signature"]])

    def test_bridge_headless_replies_reject_with_message_and_records(self):
        project, sid, tid = "af44-bridge", "ses_b", "t-b"
        exp = boundary.explain_rejection("shell", ["curl http://evil.invalid"],
                                         project)
        reqs = [{"id": "per_b", "action": "shell",
                 "resources": ["curl http://evil.invalid"]}]
        with mock.patch.object(ob, "permissions", return_value=reqs), \
             mock.patch.object(ob, "permission_reply") as reply, \
             mock.patch.object(agent_events, "record_permission_decision"), \
             mock.patch.object(boundary, "explain_rejection", return_value=exp):
            first = ob.reply_headless(project, sid, tid)
            second = ob.reply_headless(project, sid, tid)
        self.assertEqual(first["rejections"], 1)
        self.assertEqual(second["rejections"], 0)   # no duplicate reply
        self.assertEqual(reply.call_count, 1)
        self.assertEqual(reply.call_args.kwargs["message"], exp["feedback"])
        st = agent_events.policy_rejection_stats(project, sid, tid)
        self.assertEqual(st["total"], 1)


class TurnVerdictTest(unittest.TestCase):
    def test_opencode_v226_completion_shape_is_recognized(self):
        from core import action_gate
        msgs = [{"type": "idle", "outcome": "succeeded"},
                {"type": "assistant", "finish": "stop",
                 "time": {"created": 1, "completed": 1795535419690}}]
        self.assertEqual(action_gate.turn_verdict(msgs), opstatus.PASS)
        # An unfinished assistant message is still NOT evidence.
        self.assertEqual(action_gate.turn_verdict([{
            "type": "assistant", "finish": "tool-calls",
            "time": {"created": 1}}]), opstatus.NOT_TESTED)
        # A real abort stays a failure.
        self.assertEqual(action_gate.turn_verdict([{
            "type": "assistant", "finish": "error", "error": {"type": "aborted"},
            "time": {"completed": 2}}]), opstatus.FAIL)


class AgentLoopPolicyTest(unittest.TestCase):
    def test_repeated_rejection_asks_with_exact_limit(self):
        p, tid = "af44-loop", "t-loop"
        _fixture(p)
        exp = boundary.explain_rejection("shell", ["curl http://evil"], p)
        with tempfile.NamedTemporaryFile(suffix=".json") as tf:
            tf.write(b"[]"); tf.flush()
            with mock.patch.object(agent_events, "_STORE", Path(tf.name)):
                for n in (1, 2):
                    agent_events.record_policy_rejection(
                        "ses_l", "per_l%d" % n, project=p, task_id=tid,
                        action="shell", resource=["curl http://evil"],
                        explanation=exp)
                decision = agent_loop.decide(p, tid, "fix and test",
                                              {"ran": False, "ok": False},
                                              attempts=1)
        self.assertEqual(decision["action"], "ASK")
        self.assertEqual(decision["strategy"], "policy-rejection")
        self.assertIn("network-forbidden", decision["question"])
        self.assertIn("curl http://evil", decision["question"])


if __name__ == "__main__":
    unittest.main()
