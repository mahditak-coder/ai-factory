"""AF-32 — progress-based loop policy, deadlock detection, safety caps.

Real functions; external model calls are not mocked here.  The real model E2E
is reported separately (not as a mock).
"""
import json
import unittest
from pathlib import Path

import core.config as config
from core import agent_loop, agent_memory, opstatus


def _fixture(name):
    d = config.PROJECTS_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps(
        {"name": name, "type": "webpage", "version": "1.0.0"}), encoding="utf-8")
    (d / "index.html").write_text("<html></html>", encoding="utf-8")
    return d


class LoopPolicyTest(unittest.TestCase):
    def test_evidence_success_stops(self):
        p = "af32-success"
        _fixture(p)
        r = agent_loop.decide(p, "t", "fix the title in index.html",
                              {"ran": True, "ok": True, "detail": "proof"},
                              attempts=20)
        self.assertEqual(r["action"], "STOP")
        self.assertTrue(r["success"])

    def test_real_progress_allows_more_than_three_attempts(self):
        p = "af32-progress"
        _fixture(p)
        failure = {"ran": True, "ok": False, "note": "fail A in app.py"}
        for attempt in (3, 4, 5, 6):
            r = agent_loop.decide(p, "t", "fix the title in index.html",
                                  failure, attempts=attempt, progress=True)
            self.assertEqual(r["action"], "REPLAN", r)
            self.assertTrue(r["progress"])
        self.assertNotIn(r["attempts"], (0, 1, 2))

    def test_repeated_failure_without_progress_is_deadlock(self):
        p = "af32-deadlock"
        _fixture(p)
        failure = {"ran": True, "ok": False, "note": "same failure again"}
        first = agent_loop.decide(p, "t", "fix the title in index.html",
                                  failure, attempts=1, progress=True)
        self.assertEqual(first["action"], "REPLAN")
        for attempt in (3, 4):
            r = agent_loop.decide(p, "t", "fix the title in index.html",
                                  failure, attempts=attempt, progress=False)
            self.assertEqual(r["action"], "ASK", r)
            self.assertTrue(r.get("question"))
            self.assertFalse(r.get("success"))

    def test_safety_cap_and_time_budget_remain(self):
        p = "af32-caps"
        _fixture(p)
        failure = {"ran": True, "ok": False, "note": "hard cap test"}
        r = agent_loop.decide(p, "t", "fix the title in index.html", failure,
                              attempts=agent_loop.HARD_ATTEMPTS, progress=True)
        self.assertEqual(r["action"], "ASK")
        self.assertEqual(r["strategy"], "safety-cap")
        t = agent_loop.decide(p, "t", "fix the title in index.html", failure,
                              attempts=1, progress=True, elapsed_s=5,
                              max_wall_s=1)
        self.assertEqual(t["action"], "STOP")
        self.assertEqual(t["strategy"], "timeout")

    def test_no_success_without_test_evidence(self):
        p = "af32-noevidence"
        _fixture(p)
        r = agent_loop.decide(p, "t", "fix the title in index.html",
                              {"ran": False, "ok": True, "note": "no coverage"})
        self.assertFalse(r.get("success"))
        self.assertNotEqual(r["action"], "STOP")

    def test_strategy_rotation_is_real_not_cosmetic(self):
        p = "af32-strategy"
        _fixture(p)
        failure = {"ran": True, "ok": False, "note": "rotation test error"}
        got = []
        for attempt in range(3):
            r = agent_loop.decide(p, "t", "fix the title in index.html",
                                  failure, attempts=attempt, progress=True)
            got.append(r.get("strategy"))
        self.assertEqual(len(set(got)), 3, got)
        self.assertTrue(all(s in agent_loop.STRATEGIES for s in got))
