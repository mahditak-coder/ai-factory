"""AF-37 — mid-loop ASK parks and resumes the SAME task/cycle (UNIT).

Mocked model/test turns are used only here; the real E2E is reported in the
final audit. Real tasks.py/agent_events/confirmations stores are used.
"""
import json
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import agent_loop, factory, memory as memorymod, orchestrator, tasks


class MidLoopAskTest(unittest.TestCase):
    def test_ask_midloop_parks_with_context_and_resumes_same_cycle(self):
        p = "af37-loop"
        d = config.PROJECTS_DIR / p
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps(
            {"name": p, "type": "webpage", "version": "1.0.0"}), encoding="utf-8")
        (d / "index.html").write_text("<html></html>", encoding="utf-8")
        adir = config.AGENTS_DIR
        adir.mkdir(parents=True, exist_ok=True)
        seed = Path(__file__).resolve().parents[2] / "agents" / "builder.json"
        (adir / "builder.json").write_text(seed.read_text(encoding="utf-8"),
                                           encoding="utf-8")

        t = tasks.add_task(p, "fix the title in index.html",
                           agent="builder", max_attempts=2)
        tid = t["id"]
        tasks.claim_next("w1")

        red = {"ran": True, "ok": False, "note": "red",
               "contract": {"errors": [{"file": "index.html", "kind": "title",
                                        "message": "missing title"}]}}
        attempts_seen = []
        ask_decision = {"action": "ASK", "success": False,
                        "question": "کدام استراتژی؟", "options": ["A", "B"],
                        "reason": "deadlock"}

        def decide(*a, **kw):
            attempts_seen.append(kw.get("attempts"))
            return ask_decision

        with mock.patch.object(factory, "generate",
                               return_value={"session_id": "ses_x",
                                             "message_id": "msg_x"}), \
             mock.patch.object(orchestrator, "_wait_idle", return_value=True), \
             mock.patch.object(orchestrator, "oc_state", return_value={
                 "outcome": "succeeded", "running": False}), \
             mock.patch.object(factory, "testrun_combined", return_value=red), \
             mock.patch.object(factory, "fix_request", return_value={
                 "session_id": "ses_f", "message_id": "msg_f"}), \
             mock.patch.object(factory, "append_history"), \
             mock.patch.object(memorymod, "bump_agent"), \
             mock.patch.object(agent_loop, "decide", side_effect=decide):
            orchestrator._execute(tasks.get_task(tid), "w1")

        parked = tasks.get_task(tid)
        self.assertEqual(parked["state"], "awaiting_user")
        ctx = (parked.get("ask") or {}).get("context") or {}
        self.assertEqual(ctx.get("stage"), "loop")
        self.assertEqual(ctx.get("cycles"), 1)
        self.assertEqual(ctx.get("session_id"), "ses_x")
        token = parked["ask"]["token"]

        resumed = tasks.answer_task(tid, token, "A")
        self.assertEqual(resumed["state"], "queued")
        tasks.claim_next("w2")

        # After resume, generation must NOT run again; the loop continues at
        # the saved cycle.
        with mock.patch.object(factory, "generate", side_effect=AssertionError(
                "generate must not run on mid-loop resume")), \
             mock.patch.object(orchestrator, "_wait_idle", return_value=True), \
             mock.patch.object(orchestrator, "oc_state", return_value={
                 "outcome": "succeeded", "running": False}), \
             mock.patch.object(factory, "testrun_combined", return_value=red), \
             mock.patch.object(factory, "fix_request", return_value={
                 "session_id": "ses_f2", "message_id": "msg_f2"}), \
             mock.patch.object(factory, "append_history"), \
             mock.patch.object(memorymod, "bump_agent"), \
             mock.patch.object(agent_loop, "decide", return_value={
                 "action": "STOP", "success": False,
                 "reason": "unit terminal"}):
            orchestrator._execute(tasks.get_task(tid), "w2")

        self.assertEqual(attempts_seen[0], 1, attempts_seen)


    def test_cancel_while_waiting_is_honest_and_token_dies(self):
        p = "af37-cancel"
        d = config.PROJECTS_DIR / p
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps({"name": p, "type": "webpage"}),
                                     encoding="utf-8")
        t = tasks.add_task(p, "push to github", max_attempts=2)
        token = "tok-cancel-af37"
        tasks.ask_task(t["id"], "ادامه؟", ["بله"], token, "CONFIRM")
        cancelled = tasks.cancel_task(t["id"])
        self.assertEqual(cancelled["state"], "cancelled")
        with self.assertRaises(tasks.TaskError):
            tasks.answer_task(t["id"], token, "بله")


if __name__ == "__main__":
    unittest.main()
