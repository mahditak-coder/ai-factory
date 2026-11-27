# Phase 01 — real test evidence

Source: isolated rebuild clone `rebuild/phase-01-core-foundation`, base Blueprint `61240a7f35fb49586e4d739d8f9228ee86bc025a`.
Original local `~/AI-Factory` was not used for execution; its 151 uncommitted items and zero remotes were preserved.

## Commands and exact results
1. `python3 -m unittest core.tests.test_phase01_seams`
   - Result: `Ran 5 tests ... OK`.
   - Covers: non-mocked `oc_state()` delegation, duplicate `call_id` durability, stale RUNNING recovery, observable cancellation, canonical event envelope.
2. `python3 -m unittest core.tests.test_workflow_gate core.tests.test_afnext_p2_registry core.tests.test_af29_perception core.tests.test_afnext_gateway core.tests.test_core_units core.tests.test_orchestrator_loop core.tests.test_phase01_seams`
   - Result: `Ran 56 tests ... OK`.
3. `python3 -m unittest discover -s core/tests -p 'test_*.py'`
   - Result: `Ran 227 tests in 48.398s — OK` (exit 0).
   - Status: **227 PASS / 0 FAIL / 0 ERROR**.

## Targeted acceptance fix (independent review findings)
4. `python3 -m unittest core.tests.test_phase01_idempotency core.tests.test_afnext_p2_registry core.tests.test_phase01_seams`
   - Result: `Ran 17 tests in 7.411s — OK`.
5. `python3 -m unittest discover -s core/tests -p 'test_*.py'` (after both fixes)
   - Result: `Ran 231 tests in 45.527s — OK` (exit 0).
   - Status: **231 PASS / 0 FAIL / 0 ERROR**.
   - New tests use `threading.Barrier` and `threading.Event`; no timing sleeps.

## Fixes proven
- Concurrent duplicate `call_id`: 6 simultaneous requests, adapter executed exactly once; 1 PASS + 5 BLOCKED duplicate-call.
- Signature mismatch: same call_id with different inputs → `duplicate-call-signature-mismatch`.
- Bounded history truncation: 700 filler events, original receipt still rejects replay after clearing in-memory tables.
- Restart simulation: in-memory claim tables cleared, durable receipt still rejects the old call.
- Existing sequential duplicate test in `test_afnext_p2_registry` still passes.

## Final idempotency hardening (independent review — two fail-open cases)
6. `python3 -m unittest core.tests.test_phase01_idempotency -v`
   - Result: `Ran 12 tests in 4.014s — OK` (exit 0).
7. `python3 -m unittest discover -s core/tests -p 'test_*.py'`
   - Result: `Ran 239 tests in 42.861s — OK` (exit 0).
   - Status: **239 PASS / 0 FAIL / 0 ERROR**.
   - All runs used `AI_FACTORY_TEST_ROOT` pointing to a fresh isolated temporary root. A non-fatal pre-existing `ResourceWarning` (unclosed serve log) appears after the OK line.

### Required deterministic scenarios — all proven
- **Crash after durable reservation, before adapter:** a persisted `RESERVED` receipt blocks replay with `unresolved-in-progress:explicit-recovery-required`; adapter call counter stays 0.
- **Crash after side effect, before terminal receipt:** adapter runs once; forced `finalize()` failure leaves `IN_PROGRESS`; after restart the duplicate is blocked and the adapter counter stays 1.
- **Restart with unresolved `IN_PROGRESS`:** blocked without adapter invocation; `unresolved_receipts()` reports it; anonymous `reconcile()` is rejected; `reconcile(..., actor="operator-1")` reaches `TERMINAL` and no rerun occurs.
- **Missing store after initialization:** marker present + store file removed → `receipt-store-missing-after-init`, adapter not invoked.
- **Malformed JSON:** `receipt-store-corrupt-json`, adapter not invoked, corrupt file byte-for-byte unchanged (never overwritten).
- **Read/write failure:** injected `ReceiptStoreError` on `_load_receipts` and on `_save_receipts` → `receipt-store-unavailable:*`, adapter never invoked (test adapter calls `self.fail`).
- **Regressions:** concurrent 6-way same-`call_id` (1 adapter run), signature mismatch, 700-event history truncation, restart rejection, sequential duplicate — all green.

### Accurate guarantee
The durable receipt lifecycle guarantees **at-most-once execution claims**: a `call_id` is reserved on disk before any adapter runs, and no process — before or after restart — will start the adapter twice for the same call. It does **not** claim exactly-once external side effects for adapters/providers without transactional idempotency: if a process dies after the adapter produced a side effect but before the terminal receipt, the effect may already exist and cannot be undone or safely re-run by this layer. Such calls remain `IN_PROGRESS`/`RESERVED` and require explicit reconciliation with a named actor.

## Multi-process idempotency fix (independent review — cross-process race)
8. `python3 -m unittest core.tests.test_phase01_multiprocess -v`
   - Result: `Ran 7 tests in 0.729s — OK` (exit 0).
   - Stress: the same module was run 5 more times with fresh isolated roots: 5/5 exit 0, OK.
9. `python3 -m unittest discover -s core/tests -p 'test_*.py'`
   - Result: `Ran 246 tests in 43.319s — OK` (exit 0).
   - Status: **246 PASS / 0 FAIL / 0 ERROR**.

### Inter-process mechanism
- `core/tool_events.py` uses `fcntl.flock` on a stable `data/tool_receipts.json.lock` inside one `_receipt_lock()` context manager: `threading.RLock` serialises threads, `flock` serialises independent processes.
- `claim()` performs read → duplicate check → signature validation → durable RESERVED write inside that single critical section. The executor starts an adapter only when `claimed=True`.
- RESERVED → IN_PROGRESS → TERMINAL all use the same `_receipt_lock()`.
- Writers use per-writer temp names (`pid.thread.random.tmp`) plus atomic `replace`; no two writers share a temp filename.
- Lock open/acquire failure, read failure, write failure and corruption all raise `ReceiptStoreError` and block before adapter execution.

### Deterministic multiprocess scenarios — all proven
- Two processes, same `call_id`: exactly one side-effect file; 1 PASS + 1 BLOCKED; terminal receipt persisted.
- Two processes, same `call_id` but different inputs: mismatch rejected; exactly one side effect.
- `os._exit` after reservation: `RESERVED` persists; restarted process blocked; zero side effects.
- `os._exit` after side effect: `IN_PROGRESS` persists; restarted process blocked; side-effect set unchanged.
- Four processes × 25 distinct `call_id`s: 100 receipts, valid JSON, all TERMINAL, no leaked `.tmp` files.
- Injected `flock` failure: BLOCKED with `receipt-lock-*` diagnostic; zero side effects.
- `reconcile()` persists actor, reason and timestamp in the terminal receipt; anonymous reconciliation rejected.
- Earlier corruption, missing-store, read/write-failure, thread-concurrency and retention tests remain green.

### Exact multi-process guarantee and limits
**Guarantee:** On POSIX filesystems supporting `flock`, independent processes cannot both win the durable claim for one `call_id`. An adapter is started only by the process that owns the RESERVED receipt, and no process — before or after restart — starts it twice for the same call. All state transitions are serialised by the same lock; unresolved calls fail closed until explicit reconciliation by a named actor.

**Limits:** Exactly-once external side effects are still NOT claimed — if a process dies after an effect but before the terminal receipt, the effect may already exist and is neither undone nor safely re-run by this layer. `flock` is advisory: a foreign writer bypassing this module is outside the guarantee. The lock file must be on a local POSIX filesystem. On a non-POSIX platform (`fcntl` unavailable) the module fails closed and the guarantee narrows to a single process. Historical receipts evicted before the separate receipt store existed cannot be reconstructed; no destructive migration was performed.

All commands were run with `AI_FACTORY_TEST_ROOT` pointing to an isolated temporary root, so no source/project user data was modified.

## Previously recorded failure
Audit baseline recorded `test_root_isolated_when_env_set` as FAIL. It was caused by a hardcoded `/usr/tmp/opencode/af26-*` prefix in `core/tests/test_core_units.py`. Phase 01 generalized the assertion to compare `config.PROJECT_ROOT` with `config._TEST_ROOT` or the repository root. This is state-isolation scope, not a product feature change.

## Status legend
PASS — command really ran with the recorded result. FAIL — none. NOT TESTED — live providers, model calls, paid actions and browser screenshot (out of Phase 01 scope).
