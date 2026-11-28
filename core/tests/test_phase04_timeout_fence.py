"""Phase 04 timeout fence — real ToolExecutor, adapter and canonical lease."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import (confirmations, opstatus, paths, specialist_engine as se,
                  tool_events, tool_executor, tool_registry)


class TimeoutFenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.canon = self.base / "canon.json"
        self.flags = self.base / "flags.json"
        self.conf = self.base / "confirmations.json"
        self.ev = self.base / "events.json"
        self.rc = self.base / "receipts.json"
        self.ev.write_text("[]", encoding="utf-8")
        self.rc.write_text("{}", encoding="utf-8")
        self.patches = [mock.patch.object(se, "_STORE", self.canon),
                        mock.patch.object(se, "_FLAGS", self.flags),
                        mock.patch.object(confirmations, "_STORE", self.conf),
                        mock.patch.object(tool_events, "_STORE", self.ev),
                        mock.patch.object(tool_events, "_RECEIPT_STORE", self.rc)]
        for p in self.patches:
            p.start()
        se.set_engine_mode("canonical")
        self.project = "p4-timeout"
        d = config.PROJECTS_DIR / self.project
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text('{"name":"p4-timeout","type":"webapp"}',
                                     encoding="utf-8")
        self.token = se.start_run(self.project, "developer", "timeout-run")["token"]
        self.tid = "p4.timeout"
        self.calls = []

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _register(self, adapter, tid=None, scope="project:read"):
        tid = tid or self.tid
        tool_registry.registry().register(tool_registry.ToolSpec(
            tid, "P4", "test", "test", "test", scope, {}, timeout_s=10,
            adapter=adapter))
        return tid

    def _request(self, timeout_s=0.01, call_id="timeout-call", token=None):
        return tool_executor.ToolRequest(
            tool_id=self.tid, project=self.project, inputs={},
            scopes=["project:read"], role_id="developer", enforce_role=True,
            run_token=self.token if token is None else token,
            timeout_s=timeout_s, call_id=call_id)

    def test_01_timeout_keeps_fence_until_adapter_exits(self):
        entered, cancel_seen, release, side_done = (
            threading.Event(), threading.Event(), threading.Event(),
            threading.Event())
        side = paths.project_path(self.project, "spec/side-effect.txt")

        def adapter(project, inputs, **kw):
            self.calls.append(1)
            entered.set()
            kw["cancel_event"].wait(timeout=10)  # detect cancellation
            cancel_seen.set()
            release.wait(timeout=10)             # IGNORE cancellation
            side.parent.mkdir(parents=True, exist_ok=True)
            side.write_text("delayed side effect", encoding="utf-8")
            side_done.set()
            return {"ok": 1}

        self._register(adapter)
        outcome = {}

        def worker():
            outcome["result"] = tool_executor.execute(self._request())

        worker_thread = threading.Thread(target=worker)
        worker_thread.start()
        self.assertTrue(entered.wait(timeout=10))
        self.assertTrue(cancel_seen.wait(timeout=10))

        # Timeout was detected; adapter is still alive and fenced.
        self.assertTrue(tool_executor.state("timeout-call")["running"])
        receipt = tool_events.find("timeout-call")
        self.assertNotEqual(receipt.get("status"), opstatus.PASS)
        self.assertTrue(cancel_seen.is_set())
        self.assertFalse(side_done.is_set())

        superseded = threading.Event()

        def superseder():
            se.start_run(self.project, "developer", "supersede-1",
                         supersede=True)
            superseded.set()

        sup_thread = threading.Thread(target=superseder)
        sup_thread.start()
        sup_thread.join(timeout=0.05)
        self.assertFalse(superseded.is_set())
        self.assertTrue(sup_thread.is_alive())

        release.set()
        worker_thread.join(timeout=15)
        sup_thread.join(timeout=15)
        self.assertTrue(side_done.is_set())
        self.assertTrue(superseded.is_set())
        self.assertEqual(outcome["result"].status, opstatus.BLOCKED)
        self.assertEqual(outcome["result"].error, "tool-timeout")
        self.assertTrue(outcome["result"].evidence.get("waited_for_adapter_exit"))
        self.assertFalse(tool_executor.state("timeout-call")["running"])
        final = tool_events.find("timeout-call")
        self.assertEqual(final.get("status"), opstatus.BLOCKED)

    def test_02_no_terminal_receipt_while_adapter_alive(self):
        entered, release = threading.Event(), threading.Event()

        def adapter(project, inputs, **kw):
            entered.set()
            kw["cancel_event"].wait(timeout=10)
            release.wait(timeout=10)
            return {"ok": 1}

        self._register(adapter)
        outcome = {}

        def worker():
            outcome["result"] = tool_executor.execute(
                self._request(call_id="timeout-receipt"))

        t = threading.Thread(target=worker)
        t.start()
        self.assertTrue(entered.wait(timeout=10))
        receipt = tool_events.find("timeout-receipt")
        self.assertNotIn(receipt.get("status"), (opstatus.PASS, opstatus.BLOCKED))
        release.set()
        t.join(timeout=15)
        self.assertEqual(outcome["result"].status, opstatus.BLOCKED)

    def test_03_missing_token_never_reaches_adapter(self):
        self._register(lambda p, i, **k: self.calls.append(1) or {"ok": 1})
        result = tool_executor.execute(self._request(token="",
                                                     call_id="no-token"))
        self.assertEqual(result.status, opstatus.BLOCKED)
        self.assertEqual(self.calls, [])

    def test_04_normal_completion_and_exception(self):
        self._register(lambda p, i, **k: {"ok": 1})
        self.assertEqual(tool_executor.execute(
            self._request(call_id="ok-1", timeout_s=5)).status, opstatus.PASS)

        def boom(project, inputs, **kw):
            raise RuntimeError("adapter-failed")
        self._register(boom, tid="p4.boom")
        req = self._request(call_id="boom-1", timeout_s=5)
        req.tool_id = "p4.boom"
        self.assertEqual(tool_executor.execute(req).status, opstatus.FAIL)

    def test_05_receipt_store_failure_blocks_before_adapter(self):
        self._register(lambda p, i, **k: self.calls.append(1) or {"ok": 1})
        with mock.patch.object(tool_events, "claim",
                               side_effect=tool_events.ReceiptStoreError(
                                   "corrupt")):
            result = tool_executor.execute(
                self._request(call_id="rc-fail", timeout_s=5))
        self.assertEqual(result.status, opstatus.BLOCKED)
        self.assertEqual(self.calls, [])

    def test_06_infrastructure_identity_separate(self):
        # Capability requests are not specialist leases and need no run_token.
        from core import tool_identity
        from core.tests import trusted_session
        cap = tool_identity.issue_service_capability(
            "infrastructure", self.project, "project.read_file", "project:read",
            session_id=trusted_session())
        d = config.PROJECTS_DIR / self.project
        (d / "sample.txt").write_text("hello", encoding="utf-8")
        req = tool_executor.ToolRequest(
            tool_id="project.read_file", project=self.project,
            inputs={"path": "sample.txt"}, scopes=["project:read"],
            capability_token=cap, call_id="infra-1", timeout_s=5)
        self.assertEqual(tool_executor.execute(req).status, opstatus.PASS)


if __name__ == "__main__":
    unittest.main()
