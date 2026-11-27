"""Phase 01 — real inter-process idempotency tests.

Separate processes share one temporary receipt store. Synchronisation uses
``multiprocessing.Barrier``; crashes are hard ``os._exit`` calls at explicit
fault points. No timing sleeps are used as proof.
"""
import json
import multiprocessing as mp
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import opstatus, tool_events, tool_executor, tool_registry

_CTX = mp.get_context("fork")  # Termux/Linux target


def _prepare(store, receipts):
    tool_events._STORE = Path(store)
    tool_events._RECEIPT_STORE = Path(receipts)
    tool_executor._seen_calls.clear()
    tool_executor._seen_sigs.clear()
    tool_executor._running.clear()


def _side_effect(side_dir, mode):
    Path(side_dir, "effect-%d.txt" % os.getpid()).write_text("done",
                                                             encoding="utf-8")
    if mode == "after_side_effect":
        os._exit(0)  # hard crash after the effect, before terminal receipt


def _worker(store, receipts, side_dir, result_path, barrier, tool_id,
            project, call_id, inputs, mode):
    """One request in one child process; result is written as JSON."""
    try:
        _prepare(store, receipts)

        def adapter(p, i, **kw):
            _side_effect(side_dir, mode)
            return {"ok": True}

        tool_registry.registry().register(tool_registry.ToolSpec(
            tool_id, "MP", "test", "test", "test", "project:read", {},
            timeout_s=20, adapter=adapter))
        req = tool_executor.ToolRequest(
            tool_id=tool_id, project=project, inputs=dict(inputs),
            scopes=["project:read"], call_id=call_id)
        if mode == "after_reservation":
            # Crash at the exact point the durable RESERVED receipt exists.
            tool_events.reserve(call_id, tool_executor._signature(req),
                                tool_id, project)
            os._exit(0)
        if mode == "lock_fail":
            def boom(*a, **k):
                raise OSError("simulated-lock-failure")
            tool_events.fcntl.flock = boom
        barrier.wait(timeout=30)
        result = tool_executor.execute(req)
        payload = {"status": result.status, "error": result.error,
                   "pid": os.getpid()}
    except BaseException as exc:  # pragma: no cover - diagnostic payload
        payload = {"status": "EXCEPTION", "error": repr(exc), "pid": os.getpid()}
    Path(result_path).write_text(json.dumps(payload), encoding="utf-8")


def _batch_worker(store, receipts, side_dir, result_path, barrier, tool_id,
                  project, call_ids):
    """One process executing many distinct call_ids under the same shared lock."""
    try:
        _prepare(store, receipts)

        def adapter(p, i, **kw):
            _side_effect(side_dir, "normal")
            return {"ok": True}

        tool_registry.registry().register(tool_registry.ToolSpec(
            tool_id, "MP", "test", "test", "test", "project:read", {},
            timeout_s=20, adapter=adapter))
        barrier.wait(timeout=30)
        statuses = []
        for cid in call_ids:
            req = tool_executor.ToolRequest(
                tool_id=tool_id, project=project, inputs={"cid": cid},
                scopes=["project:read"], call_id=cid)
            statuses.append(tool_executor.execute(req).status)
        payload = {"count": len(statuses),
                   "pass": statuses.count(opstatus.PASS)}
    except BaseException as exc:  # pragma: no cover - diagnostic payload
        payload = {"status": "EXCEPTION", "error": repr(exc)}
    Path(result_path).write_text(json.dumps(payload), encoding="utf-8")


def _reconcile_worker(store, receipts, result_path, barrier, call_id,
                      status, actor, reason):
    """One reconciler in one child process; result is written as JSON."""
    try:
        tool_events._STORE = Path(store)
        tool_events._RECEIPT_STORE = Path(receipts)
        barrier.wait(timeout=30)
        rec = tool_events.reconcile(call_id, status, actor, reason)
        payload = {"ok": True, "status": rec.get("status"),
                   "lifecycle": rec.get("lifecycle"),
                   "reconciled_by": rec.get("reconciled_by"),
                   "reconcile_reason": rec.get("reconcile_reason"),
                   "reconciled_at": rec.get("reconciled_at")}
    except tool_events.ReceiptStoreError as exc:
        payload = {"ok": False, "error": str(exc)}
    except BaseException as exc:  # pragma: no cover - diagnostic payload
        payload = {"ok": False, "error": repr(exc)}
    Path(result_path).write_text(json.dumps(payload), encoding="utf-8")


class MultiProcessIdempotencyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = self.base / "tool_events.json"
        self.store.write_text("[]", encoding="utf-8")
        self.receipts = self.base / "tool_receipts.json"
        self.receipts.write_text("{}", encoding="utf-8")
        self.side_dir = self.base / "effects"
        self.side_dir.mkdir()
        self.ps = mock.patch.object(tool_events, "_STORE", self.store)
        self.pr = mock.patch.object(tool_events, "_RECEIPT_STORE", self.receipts)
        self.ps.start()
        self.pr.start()
        tool_executor._seen_calls.clear()
        tool_executor._seen_sigs.clear()
        tool_executor._running.clear()
        d = config.PROJECTS_DIR / "p1-mp"
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text('{"name":"p1-mp","type":"webapp"}',
                                     encoding="utf-8")
        self.project = "p1-mp"

    def tearDown(self):
        self.ps.stop()
        self.pr.stop()
        self.tmp.cleanup()

    def _effects(self):
        return list(self.side_dir.glob("effect-*.txt"))

    def _spawn(self, specs):
        barrier = _CTX.Barrier(len(specs))
        procs, paths = [], []
        for idx, spec in enumerate(specs):
            rp = self.base / ("result-%d.json" % idx)
            paths.append(rp)
            p = _CTX.Process(target=_worker, args=(
                str(self.store), str(self.receipts), str(self.side_dir),
                str(rp), barrier, spec["tool_id"], self.project,
                spec["call_id"], spec["inputs"], spec.get("mode", "normal")))
            p.start()
            procs.append(p)
        for p in procs:
            p.join(timeout=40)
            self.assertFalse(p.is_alive(), "worker process did not exit")
        return [json.loads(rp.read_text(encoding="utf-8")) if rp.exists()
                else {} for rp in paths]

    def _spawn_batches(self, batches):
        barrier = _CTX.Barrier(len(batches))
        procs, paths = [], []
        for idx, call_ids in enumerate(batches):
            rp = self.base / ("batch-%d.json" % idx)
            paths.append(rp)
            p = _CTX.Process(target=_batch_worker, args=(
                str(self.store), str(self.receipts), str(self.side_dir),
                str(rp), barrier, "mp-bulk", self.project, call_ids))
            p.start()
            procs.append(p)
        for p in procs:
            p.join(timeout=40)
            self.assertFalse(p.is_alive(), "batch worker did not exit")
        return [json.loads(rp.read_text(encoding="utf-8")) for rp in paths]

    # ------------------------------------------------------------------
    def test_two_processes_same_call_id_side_effect_at_most_once(self):
        spec = {"tool_id": "mp-shared", "call_id": "mp-once", "inputs": {}}
        results = self._spawn([dict(spec), dict(spec)])
        self.assertEqual(len(self._effects()), 1,
                         "adapter side effect must happen exactly once")
        self.assertEqual([r["status"] for r in results].count(opstatus.PASS), 1)
        self.assertEqual([r["status"] for r in results].count(opstatus.BLOCKED), 1)
        rec = tool_events.find("mp-once")
        self.assertEqual(rec["lifecycle"], "TERMINAL")
        self.assertEqual(rec["status"], opstatus.PASS)

    def test_two_processes_signature_mismatch_rejected(self):
        results = self._spawn([
            {"tool_id": "mp-shared", "call_id": "mp-sig", "inputs": {"a": 1}},
            {"tool_id": "mp-shared", "call_id": "mp-sig", "inputs": {"a": 2}},
        ])
        self.assertEqual(len(self._effects()), 1)
        self.assertEqual([r["status"] for r in results].count(opstatus.PASS), 1)
        self.assertEqual([r["status"] for r in results].count(opstatus.BLOCKED), 1)
        blocked = next(r for r in results if r["status"] == opstatus.BLOCKED)
        self.assertEqual(blocked["error"], "duplicate-call-signature-mismatch")

    def test_process_death_after_reservation_blocks_replay(self):
        self._spawn([{"tool_id": "mp-shared", "call_id": "mp-crash-res",
                      "inputs": {}, "mode": "after_reservation"}])
        self.assertEqual(self._effects(), [])
        self.assertEqual(tool_events.find("mp-crash-res")["lifecycle"],
                         "RESERVED")
        results = self._spawn([{"tool_id": "mp-shared",
                                "call_id": "mp-crash-res", "inputs": {}}])
        self.assertEqual(results[0]["status"], opstatus.BLOCKED)
        self.assertEqual(results[0]["error"],
                         "unresolved-in-progress:explicit-recovery-required")
        self.assertEqual(self._effects(), [])

    def test_process_death_after_side_effect_blocks_replay(self):
        self._spawn([{"tool_id": "mp-shared", "call_id": "mp-crash-eff",
                      "inputs": {}, "mode": "after_side_effect"}])
        self.assertEqual(len(self._effects()), 1)
        self.assertEqual(tool_events.find("mp-crash-eff")["lifecycle"],
                         "IN_PROGRESS")
        before = {p.name for p in self._effects()}
        results = self._spawn([{"tool_id": "mp-shared",
                                "call_id": "mp-crash-eff", "inputs": {}}])
        after = {p.name for p in self._effects()}
        self.assertEqual(before, after, "restart must not run the adapter again")
        self.assertEqual(results[0]["status"], opstatus.BLOCKED)
        self.assertEqual(results[0]["error"],
                         "unresolved-in-progress:explicit-recovery-required")

    def test_concurrent_reservations_and_terminals_keep_json_valid(self):
        batches = [["mp-bulk-%d-%d" % (i, j) for j in range(25)]
                   for i in range(4)]
        results = self._spawn_batches(batches)
        for r in results:
            self.assertEqual(r["count"], 25)
            self.assertEqual(r["pass"], 25)
        data = json.loads(self.receipts.read_text(encoding="utf-8"))
        self.assertEqual(len(data), 100)
        for key, rec in data.items():
            self.assertEqual(rec["call_id"], key)
            self.assertEqual(rec["lifecycle"], "TERMINAL")
            self.assertEqual(rec["status"], opstatus.PASS)
        self.assertEqual(list(self.base.glob("*.tmp")), [],
                         "no writer leaked a shared temp file")

    def test_lock_acquisition_failure_prevents_adapter(self):
        results = self._spawn([{"tool_id": "mp-shared",
                                "call_id": "mp-lockfail", "inputs": {},
                                "mode": "lock_fail"}])
        self.assertEqual(results[0]["status"], opstatus.BLOCKED)
        self.assertIn("receipt-lock", results[0]["error"])
        self.assertEqual(self._effects(), [])

    def test_two_processes_reconcile_same_call_id_exactly_one_wins(self):
        call_id = "mp-reconcile-race"
        # One unresolved receipt shared by both competing reconcilers.
        tool_events.reserve(call_id, "sig-mp-reconcile", "mp-shared",
                            self.project)
        tool_events.mark_in_progress(call_id)
        barrier = _CTX.Barrier(2)
        contenders = [("operator-A", opstatus.PASS, "approved"),
                      ("operator-B", opstatus.FAIL, "rejected")]
        procs, paths = [], []
        for idx, (actor, status, reason) in enumerate(contenders):
            rp = self.base / ("reconcile-%d.json" % idx)
            paths.append(rp)
            p = _CTX.Process(target=_reconcile_worker, args=(
                str(self.store), str(self.receipts), str(rp), barrier,
                call_id, status, actor, reason))
            p.start()
            procs.append(p)
        for p in procs:
            p.join(timeout=40)
            self.assertFalse(p.is_alive(), "reconciler did not exit")
        results = [json.loads(rp.read_text(encoding="utf-8")) for rp in paths]
        winners = [r for r in results if r.get("ok")]
        losers = [r for r in results if not r.get("ok")]
        self.assertEqual(len(winners), 1, "exactly one reconciliation wins")
        self.assertEqual(len(losers), 1)
        self.assertIn("already-reconciled", losers[0]["error"])
        winner = winners[0]
        rec = tool_events.find(call_id)
        # Persisted audit record is internally consistent with the winner.
        self.assertEqual(rec["call_id"], call_id)
        self.assertEqual(rec["lifecycle"], "TERMINAL")
        self.assertEqual(rec["status"], winner["status"])
        self.assertEqual(rec["reconciled_by"], winner["reconciled_by"])
        self.assertEqual(rec["reconcile_reason"], winner["reconcile_reason"])
        self.assertEqual(rec["reconciled_at"], winner["reconciled_at"])
        self.assertTrue(rec["reconciled_at"])
        self.assertIn(rec["reconciled_by"], ("operator-A", "operator-B"))

    def test_reconcile_audit_trail_persists_actor_and_reason(self):
        tool_events.reserve("mp-rec", "sig-mp-rec", "mp-shared", self.project)
        tool_events.mark_in_progress("mp-rec")
        with self.assertRaises(tool_events.ReceiptStoreError):
            tool_events.reconcile("mp-rec", opstatus.FAIL, "")
        tool_events.reconcile("mp-rec", opstatus.FAIL, "operator-2", "reviewed")
        rec = tool_events.find("mp-rec")
        self.assertEqual(rec["lifecycle"], "TERMINAL")
        self.assertEqual(rec["reconciled_by"], "operator-2")
        self.assertEqual(rec["reconcile_reason"], "reviewed")
        self.assertTrue(rec["reconciled_at"])


if __name__ == "__main__":
    unittest.main()
