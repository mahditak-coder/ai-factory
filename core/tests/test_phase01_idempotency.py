"""Phase 01 acceptance fix — concurrent and restart-safe idempotency."""
import secrets, tempfile, threading, unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import opstatus, tool_events, tool_executor, tool_registry


class IdempotencyTest(unittest.TestCase):
    def setUp(self):
        self.ev_file = tempfile.NamedTemporaryFile(suffix=".json")
        Path(self.ev_file.name).write_text("[]", encoding="utf-8")
        self.rc_file = tempfile.NamedTemporaryFile(suffix=".json")
        Path(self.rc_file.name).write_text("{}", encoding="utf-8")
        self.pe = mock.patch.object(tool_events, "_STORE", Path(self.ev_file.name))
        self.pr = mock.patch.object(tool_events, "_RECEIPT_STORE", Path(self.rc_file.name))
        self.pe.start(); self.pr.start()
        tool_executor._seen_calls.clear(); tool_executor._seen_sigs.clear()
        d = config.PROJECTS_DIR / "p1-idem"
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text('{"name":"p1-idem","type":"webapp"}')
        self.project = "p1-idem"

    def tearDown(self):
        self.pe.stop(); self.pr.stop()
        self.ev_file.close(); self.rc_file.close()
        tool_executor._seen_calls.clear(); tool_executor._seen_sigs.clear()

    def _register(self, adapter):
        tid = "p1-idem-" + secrets.token_hex(6)
        tool_registry.registry().register(tool_registry.ToolSpec(
            tid, "P1", "test", "test", "test", "project:read", {},
            timeout_s=10, adapter=adapter))
        return tid

    def _request(self, tool_id, call_id, extra=None):
        return tool_executor.ToolRequest(tool_id=tool_id, project=self.project,
            inputs=dict(extra or {}), scopes=["project:read"], call_id=call_id)

    def test_concurrent_same_call_id_executes_once(self):
        calls = []
        lock = threading.Lock()
        entered = threading.Event()
        release = threading.Event()

        def adapter(project, inputs, **kw):
            with lock:
                calls.append(1)
            entered.set()
            release.wait(timeout=5)
            return {"ok": True}

        tid = self._register(adapter)
        req = self._request(tid, "p1-concurrent")
        count = 6
        start = threading.Barrier(count)
        results = []

        def worker():
            start.wait(timeout=5)
            results.append(tool_executor.execute(req))

        threads = [threading.Thread(target=worker) for _ in range(count)]
        for t in threads: t.start()
        try:
            self.assertTrue(entered.wait(timeout=5), "adapter never entered")
        finally:
            release.set()
        for t in threads: t.join(timeout=10)
        self.assertEqual(len(results), count)
        self.assertEqual(len(calls), 1)
        statuses = [r.status for r in results]
        self.assertEqual(statuses.count(opstatus.PASS), 1)
        self.assertEqual(statuses.count(opstatus.BLOCKED), count - 1)

    def test_signature_mismatch_rejected(self):
        tid = self._register(lambda p, i, **k: {"ok": True})
        first = tool_executor.execute(self._request(tid, "p1-sig", {"a": 1}))
        second = tool_executor.execute(self._request(tid, "p1-sig", {"a": 2}))
        self.assertEqual(first.status, opstatus.PASS)
        self.assertEqual(second.status, opstatus.BLOCKED)
        self.assertEqual(second.error, "duplicate-call-signature-mismatch")

    def test_history_truncation_keeps_receipt(self):
        tid = self._register(lambda p, i, **k: {"ok": True})
        req = self._request(tid, "p1-old")
        first = tool_executor.execute(req)
        self.assertEqual(first.status, opstatus.PASS)
        for i in range(700):
            tool_events.record({"call_id": "filler-%d" % i, "tool": "fill",
                                "status": opstatus.PASS})
        self.assertLessEqual(len(tool_events.events(limit=5000)), 600)
        tool_executor._seen_calls.clear(); tool_executor._seen_sigs.clear()
        again = tool_executor.execute(req)
        self.assertEqual(again.status, opstatus.BLOCKED)
        self.assertEqual(again.error, "duplicate-call")
        self.assertTrue(again.evidence.get("durable"))

    def test_restart_simulation_rejects_old_call(self):
        tid = self._register(lambda p, i, **k: {"ok": True})
        req = self._request(tid, "p1-restart")
        self.assertEqual(tool_executor.execute(req).status, opstatus.PASS)
        # Simulate process restart: in-memory claim tables are gone, durable
        # receipt store is not.
        tool_executor._seen_calls.clear(); tool_executor._seen_sigs.clear()
        tool_executor._running.clear()
        after = tool_executor.execute(req)
        self.assertEqual(after.status, opstatus.BLOCKED)
        self.assertEqual(after.error, "duplicate-call")


if __name__ == "__main__":
    unittest.main()
