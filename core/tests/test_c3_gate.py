"""C3 — deterministic reproduction of the plan-approval gate race.

The defect: the worker published `awaiting_approval` BEFORE installing the
task hold.  approve() could clear a hold that did not exist yet, after which
the worker installed it permanently -> task stuck with hold="approval" and no
recovery path.  The fix installs the hold first.  Both interleavings are
asserted here using the real pipeline/tasks functions.
"""
import json
import tempfile
import unittest
from pathlib import Path

import core.config as config
from core import pipeline, tasks


PROJECT = "c3_gate_probe"


class PlanGateRaceTest(unittest.TestCase):
    def setUp(self):
        # Seed the default agent into the isolated tree (fixture, not live data).
        adir = config.AGENTS_DIR
        adir.mkdir(parents=True, exist_ok=True)
        seed = Path(__file__).resolve().parents[2] / "agents" / "builder.json"
        (adir / "builder.json").write_text(seed.read_text(encoding="utf-8"),
                                           encoding="utf-8")
        d = config.PROJECTS_DIR / PROJECT
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(
            json.dumps({"name": PROJECT, "type": "webpage", "version": "1.0.0"}),
            encoding="utf-8")
        (d / "index.html").write_text("<html><title>gate</title></html>",
                                      encoding="utf-8")
        self.tmp = Path(tempfile.mkdtemp(prefix="af26-c3-"))
        self._old_task_store, self._old_pipe_store = tasks._STORE, pipeline._STORE
        tasks._STORE = self.tmp / "tasks.json"
        pipeline._STORE = self.tmp / "pipelines.json"
        created = pipeline.create(PROJECT, "یک صفحه وب بساز")
        self.pid, self.tid = created["id"], created["task_id"]
        claimed = tasks.claim_next("unit-worker")   # worker owns it, as in reality
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["id"], self.tid)

    def tearDown(self):
        tasks._STORE, pipeline._STORE = self._old_task_store, self._old_pipe_store

    def _publish_awaiting(self):
        recs = pipeline._load()
        rec = pipeline._get(recs, self.pid)
        rec["state"] = "awaiting_approval"
        rec["stage"] = "generate"
        rec["done"] = {"plan": {"at": "unit-test"}}
        rec["plan_text"] = "برنامهٔ آزمونی"
        pipeline._save(recs)
        return rec

    def test_approve_after_gate_is_published_clears_the_hold(self):
        # Fixed ordering: hold first, then publish awaiting state.
        tasks.set_hold(self.tid, "approval")
        self._publish_awaiting()
        tasks.release_task(self.tid, "در انتظار تأیید")

        result = pipeline.approve(self.pid, True)
        self.assertEqual(result["state"], "approved", result)
        task = tasks.get_task(self.tid)
        self.assertIsNone(task.get("hold"),
                          "hold stayed installed after approval — pipeline is stranded")
        # The task must be claimable again (no invisible blocker).
        claimed = tasks.claim_next("unit-worker")
        self.assertIsNotNone(claimed, "approved task could not be claimed")
        self.assertEqual(claimed["id"], self.tid)

    def test_approve_before_gate_is_published_is_rejected_then_retry_works(self):
        # An early approve must not clear a hold it cannot see yet.
        tasks.set_hold(self.tid, "approval")
        with self.assertRaises(pipeline.PipelineError) as ctx:
            pipeline.approve(self.pid, True)
        self.assertEqual(ctx.exception.status, 409)

        self._publish_awaiting()
        tasks.release_task(self.tid, "در انتظار تأیید")
        approved = pipeline.approve(self.pid, True)
        self.assertEqual(approved["state"], "approved")
        self.assertIsNone(tasks.get_task(self.tid).get("hold"))

    def test_reject_is_terminal_and_cancels_task(self):
        tasks.set_hold(self.tid, "approval")
        self._publish_awaiting()
        rejected = pipeline.approve(self.pid, False)
        self.assertEqual(rejected["state"], "rejected")
        self.assertEqual(tasks.get_task(self.tid)["state"], "cancelled")


if __name__ == "__main__":
    unittest.main()
