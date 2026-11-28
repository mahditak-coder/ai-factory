"""Phase 01 — core seams: delegation, replay, recovery, cancellation, envelope."""
import tempfile, threading, unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import (event_envelope, model_gateway, opstatus, orchestrator,
                  tool_events, tool_executor, tool_registry, workflow as wf)
from core.tests import trusted_request


class Phase01Seams(unittest.TestCase):
    def setUp(self):
        self.tf = tempfile.NamedTemporaryFile(suffix=".json")
        Path(self.tf.name).write_text("[]", encoding="utf-8")
        self.tp = mock.patch.object(tool_events, "_STORE", Path(self.tf.name))
        self.tp.start()
        self.wf_tf = tempfile.NamedTemporaryFile(suffix=".json")
        Path(self.wf_tf.name).write_text("{}", encoding="utf-8")
        self.wp = mock.patch.object(wf, "_STORE", Path(self.wf_tf.name))
        self.wp.start()
        d = config.PROJECTS_DIR / "p1-seams"
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text('{"name":"p1-seams","type":"webapp"}')

    def tearDown(self):
        self.tp.stop(); self.wp.stop()
        self.tf.close(); self.wf_tf.close()
        model_gateway.set_gateway(None)

    def test_oc_state_delegates_without_patching_symbol(self):
        sentinel = {"running": False, "state": "idle"}
        class Fake(model_gateway.ModelGateway):
            def state(self, session_id: str) -> dict:
                return dict(sentinel, session_id=session_id)
        model_gateway.set_gateway(Fake(), strict=True)
        out = orchestrator.oc_state("ses-direct")
        self.assertEqual(out["state"], "idle")
        self.assertEqual(out["session_id"], "ses-direct")

    def test_duplicate_call_id_never_executes_twice(self):
        calls = []
        tid = "p1-echo"
        spec = tool_registry.ToolSpec(tid, "P1 echo", "test", "test", "test",
            "project:read", {}, timeout_s=5, adapter=lambda p, i, **k: calls.append(1) or {"ok": True})
        tool_registry.registry().register(spec)
        req = trusted_request(tool_id=tid, project="p1-seams",
            inputs={}, scopes=["project:read"], call_id="p1-dup-1")
        first = tool_executor.execute(req)
        second = tool_executor.execute(req)
        self.assertEqual(first.status, opstatus.PASS, first.error)
        self.assertEqual(second.status, opstatus.BLOCKED)
        self.assertEqual(second.error, "duplicate-call")
        self.assertEqual(len(calls), 1)

    def test_stale_running_stage_is_recovered(self):
        wf.create("p1-seams", "webapp", "test")
        items = wf._load()
        rec = next(iter(items.values()))
        st = rec["stages"][0]
        st["status"] = wf.RUNNING
        st["run_pid"] = 999999
        st["run_token"] = "orphan"
        rec["status"] = wf.RUNNING
        rec["current_stage"] = st["id"]
        wf._save(items)
        recovered = wf.recover_stale("p1-seams")
        self.assertEqual(recovered, [st["id"]])
        snap = wf.state("p1-seams")
        self.assertEqual(snap["stages"][0]["status"], wf.REVISION)
        self.assertEqual(snap["status"], wf.RUNNING)
        self.assertTrue(any(h["action"] == "stale-run-recovered"
                            for h in snap["history"]))

    def test_cancel_request_is_observable_before_terminal_result(self):
        evt = threading.Event()
        tool_executor._running["p1-cancel"] = evt
        try:
            self.assertTrue(tool_executor.cancel("p1-cancel"))
            snap = tool_executor.state("p1-cancel")
            self.assertTrue(snap["cancel_requested"])
            self.assertFalse(snap["cancel_acknowledged"])
            receipt = tool_events.find("cancel-p1-cancel")
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt["error"], "cancel-requested")
        finally:
            tool_executor._running.pop("p1-cancel", None)

    def test_event_envelope_fields_and_validation(self):
        rec = tool_events.record({"call_id": "p1-env", "tool": "p1",
                                  "status": "success", "project": "p1-seams"})
        self.assertEqual(rec["schema_version"], event_envelope.SCHEMA_VERSION)
        self.assertTrue(rec["event_uid"] and rec["seq"])
        self.assertEqual(rec["status"], opstatus.PASS)
        self.assertIsNone(event_envelope.validate(rec))


if __name__ == "__main__":
    unittest.main()
