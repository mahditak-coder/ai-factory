"""Phase 01 final idempotency hardening — deterministic fault-injection tests.

Covers the durable RESERVED/IN_PROGRESS/TERMINAL receipt lifecycle, fail-closed
receipt-store handling, and all earlier concurrency/restart regressions.
No timing sleeps are used: concurrency uses Barrier/Event, crashes are
simulated with explicit fault injection and direct lifecycle calls.
"""
import secrets
import tempfile
import threading
import unittest
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
        self.pr = mock.patch.object(tool_events, "_RECEIPT_STORE",
                                    Path(self.rc_file.name))
        self.pe.start()
        self.pr.start()
        self._clear_memory()
        d = config.PROJECTS_DIR / "p1-idem"
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text('{"name":"p1-idem","type":"webapp"}',
                                     encoding="utf-8")
        self.project = "p1-idem"

    def tearDown(self):
        self.pe.stop()
        self.pr.stop()
        self.ev_file.close()
        self.rc_file.close()
        self._clear_memory()

    @staticmethod
    def _clear_memory():
        tool_executor._seen_calls.clear()
        tool_executor._seen_sigs.clear()
        tool_executor._running.clear()

    def _register(self, adapter):
        tid = "p1-idem-" + secrets.token_hex(6)
        tool_registry.registry().register(tool_registry.ToolSpec(
            tid, "P1", "test", "test", "test", "project:read", {},
            timeout_s=10, adapter=adapter))
        return tid

    def _request(self, tool_id, call_id, extra=None):
        return tool_executor.ToolRequest(
            tool_id=tool_id, project=self.project,
            inputs=dict(extra or {}), scopes=["project:read"],
            call_id=call_id)

    def _sig(self, req):
        return tool_executor._signature(req)

    # ------------------------------------------------------------------
    # Required new tests: durable lifecycle and fail-closed store
    # ------------------------------------------------------------------
    def test_crash_after_reservation_before_adapter_blocks(self):
        """A crash leaving a RESERVED receipt must never run the adapter."""
        calls = []
        tid = self._register(lambda p, i, **k: calls.append(1) or {"ok": True})
        req = self._request(tid, "p1-crash-reserved")
        sig = self._sig(req)
        # Simulate the crash point: reservation was persisted, adapter not run.
        tool_events.reserve(req.call_id, sig, req.tool_id, req.project)
        self._clear_memory()  # process restart loses in-memory claims
        result = tool_executor.execute(req)
        self.assertEqual(result.status, opstatus.BLOCKED)
        self.assertEqual(calls, [])
        self.assertEqual(result.error,
                         "unresolved-in-progress:explicit-recovery-required")

    def test_crash_after_side_effect_before_terminal_receipt(self):
        """Side effect happened once; no terminal receipt -> no second run."""
        calls = []
        tid = self._register(lambda p, i, **k: calls.append(1) or {"ok": True})
        req = self._request(tid, "p1-crash-sideeffect")

        def crash_before_terminal(*a, **k):
            raise tool_events.ReceiptStoreError("simulated-crash-before-terminal")

        with mock.patch.object(tool_events, "finalize", crash_before_terminal):
            first = tool_executor.execute(req)
        self.assertEqual(len(calls), 1, "side effect must happen exactly once")
        self.assertEqual(first.status, opstatus.BLOCKED)
        receipt = tool_events.find(req.call_id)
        self.assertEqual(receipt.get("lifecycle"), "IN_PROGRESS")
        self._clear_memory()  # restart
        second = tool_executor.execute(req)
        self.assertEqual(second.status, opstatus.BLOCKED)
        self.assertEqual(len(calls), 1, "adapter must not run again")
        self.assertEqual(second.error,
                         "unresolved-in-progress:explicit-recovery-required")

    def test_restart_unresolved_in_progress_and_explicit_reconciliation(self):
        """Unresolved IN_PROGRESS fails closed; only explicit actor recovers."""
        calls = []
        tid = self._register(lambda p, i, **k: calls.append(1) or {"ok": True})
        req = self._request(tid, "p1-unresolved")
        sig = self._sig(req)
        tool_events.reserve(req.call_id, sig, req.tool_id, req.project)
        tool_events.mark_in_progress(req.call_id)
        self._clear_memory()
        # Diagnostics expose the unresolved receipt.
        unresolved = tool_events.unresolved_receipts()
        self.assertTrue(any(r["call_id"] == req.call_id for r in unresolved))
        # Duplicate is blocked, adapter is never invoked.
        blocked = tool_executor.execute(req)
        self.assertEqual(blocked.status, opstatus.BLOCKED)
        self.assertEqual(calls, [])
        # Anonymous reconciliation is rejected.
        with self.assertRaises(tool_events.ReceiptStoreError):
            tool_events.reconcile(req.call_id, opstatus.FAIL, "")
        # Explicit actor reconciles to a terminal outcome; no adapter rerun.
        tool_events.reconcile(req.call_id, opstatus.FAIL, "operator-1",
                              "manual-review")
        rec = tool_events.find(req.call_id)
        self.assertEqual(rec.get("lifecycle"), "TERMINAL")
        self.assertEqual(rec.get("status"), opstatus.FAIL)
        self.assertEqual(tool_events.unresolved_receipts(), [])
        again = tool_executor.execute(req)
        self.assertEqual(again.status, opstatus.BLOCKED)
        self.assertEqual(again.error, "duplicate-call")
        self.assertEqual(calls, [])

    def test_missing_store_after_initialization_fails_closed(self):
        tool_events.initialize_receipt_store()
        Path(self.rc_file.name).unlink()
        self.assertTrue(Path(str(self.rc_file.name) + ".initialized").exists())
        calls = []
        tid = self._register(lambda p, i, **k: calls.append(1) or {"ok": True})
        result = tool_executor.execute(self._request(tid, "p1-missing"))
        self.assertEqual(result.status, opstatus.BLOCKED)
        self.assertIn("missing-after-init", result.error)
        self.assertEqual(calls, [])

    def test_malformed_json_fails_closed_without_overwrite(self):
        tool_events.initialize_receipt_store()
        bad = '{ "p1-corrupt": '
        Path(self.rc_file.name).write_text(bad, encoding="utf-8")
        calls = []
        tid = self._register(lambda p, i, **k: calls.append(1) or {"ok": True})
        result = tool_executor.execute(self._request(tid, "p1-corrupt"))
        self.assertEqual(result.status, opstatus.BLOCKED)
        self.assertIn("receipt-store", result.error)
        self.assertEqual(calls, [])
        self.assertEqual(Path(self.rc_file.name).read_text(encoding="utf-8"),
                         bad, "corrupt store must not be overwritten")

    def test_store_read_failure_blocks_before_adapter(self):
        tid = self._register(lambda p, i, **k: self.fail("adapter must not run"))
        with mock.patch.object(tool_events, "_load_receipts",
                               side_effect=tool_events.ReceiptStoreError(
                                   "receipt-store-unreadable:PermissionError")):
            result = tool_executor.execute(self._request(tid, "p1-readfail"))
        self.assertEqual(result.status, opstatus.BLOCKED)
        self.assertIn("receipt-store-unavailable", result.error)

    def test_store_write_failure_blocks_before_adapter(self):
        tid = self._register(lambda p, i, **k: self.fail("adapter must not run"))
        with mock.patch.object(tool_events, "_save_receipts",
                               side_effect=tool_events.ReceiptStoreError(
                                   "receipt-store-write-failed:OSError")):
            result = tool_executor.execute(self._request(tid, "p1-writefail"))
        self.assertEqual(result.status, opstatus.BLOCKED)
        self.assertIn("receipt-store-unavailable", result.error)

    # ------------------------------------------------------------------
    # Required regressions: concurrency, signature, retention, restart
    # ------------------------------------------------------------------
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
        for t in threads:
            t.start()
        try:
            self.assertTrue(entered.wait(timeout=5), "adapter never entered")
        finally:
            release.set()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(len(results), count)
        self.assertEqual(len(calls), 1)
        self.assertEqual([r.status for r in results].count(opstatus.PASS), 1)
        self.assertEqual([r.status for r in results].count(opstatus.BLOCKED),
                         count - 1)

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
        self.assertEqual(tool_executor.execute(req).status, opstatus.PASS)
        for i in range(700):
            tool_events.record({"call_id": "filler-%d" % i, "tool": "fill",
                                "status": opstatus.PASS})
        self.assertLessEqual(len(tool_events.events(limit=5000)), 600)
        self._clear_memory()
        again = tool_executor.execute(req)
        self.assertEqual(again.status, opstatus.BLOCKED)
        self.assertEqual(again.error, "duplicate-call")

    def test_restart_simulation_rejects_old_call(self):
        tid = self._register(lambda p, i, **k: {"ok": True})
        req = self._request(tid, "p1-restart")
        self.assertEqual(tool_executor.execute(req).status, opstatus.PASS)
        self._clear_memory()
        after = tool_executor.execute(req)
        self.assertEqual(after.status, opstatus.BLOCKED)
        self.assertEqual(after.error, "duplicate-call")

    def test_sequential_duplicate_still_refused(self):
        tid = self._register(lambda p, i, **k: {"ok": True})
        req = self._request(tid, "p1-seq")
        self.assertEqual(tool_executor.execute(req).status, opstatus.PASS)
        self.assertEqual(tool_executor.execute(req).status, opstatus.BLOCKED)


if __name__ == "__main__":
    unittest.main()
