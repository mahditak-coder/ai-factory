"""AF-31 — intelligent memory, dynamic planning, bounded autonomous decisions.

Real functions on real (isolated) filesystem stores.  No OpenCode/network is
substituted by mocks here; unavailable E2E paths are reported separately.
"""
import json
import time
import unittest
from pathlib import Path

import core.config as config
from core import (agent_loop, agent_memory, agent_planner, agent_policy,
                  factory, opstatus, project_snapshot)


def _fixture(name: str) -> Path:
    d = config.PROJECTS_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(
        json.dumps({"name": name, "type": "webpage", "version": "1.0.0"}),
        encoding="utf-8")
    (d / "index.html").write_text("<html><title>af31</title><h1>x</h1></html>",
                                  encoding="utf-8")
    return d


class MemoryTest(unittest.TestCase):
    def test_persistence_isolation_and_relevance(self):
        a, b = "af31-mem-a", "af31-mem-b"
        _fixture(a); _fixture(b)
        agent_memory.remember(a, "decision", "use stdlib server for alpha",
                              task_id="t1", tags=["alpha"])
        agent_memory.remember(a, "decision", "irrelevant zebra note",
                              task_id="t2", tags=["zebra"])
        agent_memory.remember(b, "error", "project B failure",
                              task_id="t3", tags=["beta"])
        # Different "session": memory survives.
        got = agent_memory.recall(a, "alpha server", session_id="ses_new")
        self.assertIn("alpha", got)
        self.assertNotIn("irrelevant zebra", got)
        self.assertNotIn("project B", got)
        self.assertNotIn("beta", agent_memory.recall(b, "alpha server"))

    def test_stale_invalid_and_sourceless_entries_are_not_injected(self):
        p = "af31-mem-stale"
        _fixture(p)
        agent_memory.remember(p, "error", "stale failure", tags=["stale"], ttl_s=-1)
        agent_memory.remember(p, "decision", "sourceless tool result",
                              source="evt-does-not-exist")
        agent_memory.remember(p, "error", "real failure", tags=["fresh"])
        text = agent_memory.recall(p, "failure")
        self.assertNotIn("stale failure", text)
        self.assertIn("real failure", text)
        agent_memory.invalidate(p, "")  # no-op guard
        before = agent_memory.stats(p)[p]["total"]
        self.assertGreaterEqual(before, 1)

    def test_secret_memory_is_refused(self):
        p = "af31-mem-secret"
        _fixture(p)
        r = agent_memory.remember(p, "decision", "api_key=sk-SECRET123456789")
        self.assertFalse(r["ok"])
        self.assertNotIn("SECRET", agent_memory.recall(p, "api_key"))


class PlannerTest(unittest.TestCase):
    def test_plan_steps_dependencies_and_replan(self):
        p = "af31-plan"
        _fixture(p)
        snap = project_snapshot.build(p)
        plan = agent_planner.create(p, "fix the page", snapshot=snap)
        first = agent_planner.next_step(plan)
        self.assertEqual(first["id"], "inspect")
        plan = agent_planner.mark(plan["id"], "inspect", opstatus.PASS, "snapshot ok")
        self.assertEqual(agent_planner.next_step(plan)["id"], "implement")
        plan = agent_planner.mark(plan["id"], "implement", opstatus.PASS, "files written")
        plan = agent_planner.mark(plan["id"], "verify", opstatus.FAIL, "red tests")
        agent_planner.replan(plan["id"], "red tests", failed_step="verify")
        updated = agent_planner.get(plan["id"])
        self.assertEqual(updated["replans"], 1)
        self.assertEqual(updated["status"], "QUEUED")
        self.assertEqual(agent_planner.get(plan["id"])["steps"][0]["status"],
                         opstatus.PASS, "a green step must never be repeated")
        dialog = agent_planner.to_prompt(updated)
        self.assertIn("verify", dialog)

    def test_question_blocks_and_answer_resumes(self):
        p = "af31-ask"
        _fixture(p)
        plan = agent_planner.create(
            p, "کجاست؟", decision={"decision": "ASK", "question": "کدام فایل؟"})
        self.assertEqual(plan["status"], opstatus.BLOCKED)
        self.assertIsNone(agent_planner.next_step(plan))
        resumed = agent_planner.answer(plan["id"], "index.html")
        self.assertEqual(resumed["status"], "QUEUED")
        self.assertEqual(agent_planner.next_step(resumed)["id"], "inspect")


class PolicyTest(unittest.TestCase):
    def test_sensitive_vs_vague_vs_routine(self):
        self.assertEqual(agent_policy.assess("push to github")["decision"],
                         agent_policy.CONFIRM)
        question = agent_policy.assess("همینطور")
        self.assertEqual(question["decision"], agent_policy.ASK)
        self.assertTrue(question["options"])
        self.assertEqual(agent_policy.assess("fix the title in index.html")["decision"],
                         agent_policy.PROCEED)
        assumed = agent_policy.assess("کمی بهترش کن")
        self.assertEqual(assumed["decision"], agent_policy.ASSUME)
        self.assertTrue(assumed.get("assumption"))

    def test_wrong_claim_is_corrected_with_evidence(self):
        out = agent_policy.correct("تست سبز است",
                                   {"verification": {"status": "FAIL"}})
        self.assertFalse(out["agree"])
        self.assertIn("FAIL", out["reply"])
        self.assertTrue(agent_policy.correct("تست سبز است",
                        {"verification": {"status": "PASS"}})["agree"])


class LoopTest(unittest.TestCase):
    def test_success_requires_evidence_failure_changes_strategy_loops_stop(self):
        p, tid = "af31-loop", "t-loop"
        _fixture(p)
        ok = agent_loop.decide(p, tid, "fix the title in index.html",
                               {"ran": True, "ok": True, "detail": "evidence"},
                               attempts=0)
        self.assertEqual(ok["action"], "STOP")
        self.assertTrue(ok["success"])

        bad = {"ran": True, "ok": False, "note": "syntax error in app.py"}
        first = agent_loop.decide(p, tid, "fix the title in index.html", bad, attempts=0)
        self.assertEqual(first["action"], "REPLAN")
        self.assertIn(first["strategy"], ("narrow-scope", "alternate-tool", "inspect-first"))
        # Same real failure again: a DIFFERENT strategy, never blind repetition.
        second = agent_loop.decide(p, tid, "fix the title in index.html", bad, attempts=1)
        self.assertNotEqual(second.get("strategy"), first.get("strategy"))
        third = agent_loop.decide(p, tid, "fix the title in index.html", bad, attempts=2)
        fourth = agent_loop.decide(p, tid, "fix the title in index.html", bad,
                                   attempts=3, progress=False)
        self.assertIn(fourth["action"], ("ASK", "STOP"))
        self.assertFalse(fourth.get("success"))

        timeout = agent_loop.decide(p, tid, "fix the title in index.html", bad,
                                    elapsed_s=agent_loop.MAX_WALL_S + 1)
        self.assertEqual(timeout["action"], "STOP")
        no_evidence = agent_loop.decide(p, tid, "fix the title in index.html",
                                        {"ran": False, "ok": True})
        self.assertFalse(no_evidence.get("success"))


class PerceptionIntegrationTest(unittest.TestCase):
    def test_memory_and_plan_reach_the_model_context(self):
        p = "af31-perception"
        _fixture(p)
        agent_memory.remember(p, "decision", "persisted decision about title")
        plan = agent_planner.create(p, "fix title")
        text = factory.perception_block(p, "ses_x", "t1", "fix title", plan)
        self.assertIn("persisted decision about title", text)
        self.assertIn("برنامه", text)
        self.assertIn("inspect", text)


if __name__ == "__main__":
    unittest.main()
