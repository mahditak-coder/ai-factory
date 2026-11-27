"""AF-34 — UNIT test of the Orchestrator multi-cycle wiring.

Mocked model/test turns are used ONLY here to drive deterministic red/green
sequences; this is NOT an end-to-end proof.  The real E2E result is reported
separately with its actual cycle count.
"""
import json
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import memory as memorymod, orchestrator, tasks


class OrchestratorLoopTest(unittest.TestCase):
    def test_five_red_green_cycles_are_driven_by_real_evidence_flag(self):
        name = "af34-unit-loop"
        d = config.PROJECTS_DIR / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps(
            {"name": name, "type": "webpage", "version": "1.0.0"}), encoding="utf-8")
        (d / "index.html").write_text("<html></html>", encoding="utf-8")
        adir = config.AGENTS_DIR
        adir.mkdir(parents=True, exist_ok=True)
        seed = Path(__file__).resolve().parents[2] / "agents" / "builder.json"
        (adir / "builder.json").write_text(seed.read_text(encoding="utf-8"),
                                           encoding="utf-8")

        task = tasks.add_task(name, "fix the title in index.html",
                              agent="builder", max_attempts=2)
        tid = task["id"]
        tasks.claim_next("w1")

        replies = [
            {"ran": True, "ok": False, "note": "red A", "contract": {
                "errors": [{"file": "index.html", "kind": "title",
                            "message": "missing title A"}]}},
            {"ran": True, "ok": False, "note": "red B", "contract": {
                "errors": [{"file": "index.html", "kind": "h1",
                            "message": "missing h1 B"}]}},
            {"ran": True, "ok": False, "note": "red C", "contract": {
                "errors": [{"file": "app.js", "kind": "button",
                            "message": "missing button C"}]}},
            {"ran": True, "ok": False, "note": "red D", "contract": {
                "errors": [{"file": "data.json", "kind": "value",
                            "message": "wrong value D"}]}},
            {"ran": True, "ok": True, "note": "green", "contract": {"errors": []}},
        ]
        calls = {"n": 0}
        def fake_testrun(_project):
            i = min(calls["n"], len(replies) - 1)
            calls["n"] += 1
            return replies[i]

        finished = []
        with mock.patch.object(orchestrator.factory, "generate",
                               return_value={"session_id": "ses_unit"} ), \
             mock.patch.object(orchestrator, "_wait_idle", return_value=True), \
             mock.patch.object(orchestrator, "oc_state", return_value={
                 "outcome": "succeeded", "running": False}), \
             mock.patch.object(orchestrator.factory, "testrun_combined",
                               side_effect=fake_testrun), \
             mock.patch.object(orchestrator.factory, "fix_request",
                               return_value={"session_id": "ses_fix"}), \
             mock.patch.object(orchestrator.factory, "append_history"), \
             mock.patch.object(memorymod, "bump_agent"), \
             mock.patch.object(tasks, "finish_task",
                               side_effect=lambda t, r: finished.append(r) or t):
            orchestrator._execute(tasks.get_task(tid), "w1")

        self.assertEqual(len(finished), 1, "task must finish green")
        result = finished[0]
        self.assertEqual(result["cycles"], 5, result)
        self.assertEqual(calls["n"], 5)
        self.assertEqual(len(result["history"]), 4)
        self.assertTrue(all(h["decision"] in ("REPLAN", "CONTINUE")
                            for h in result["history"]), result["history"])


if __name__ == "__main__":
    unittest.main()
