# Current State

> خلاصه فارسی: فاز ۰۱real اجرا شد؛ چهار ایراد پذیرش شناسایی شده توسط بازبینی مستقل real اصلاح و آزمایش شدند (race همزمان، ماندگاری receipt، پنجره سقوط process، و فساد/دسترسناپذیری store). اکنون منتظر بازبینی مستقل نهایی هستیم و مرحله بعدی آغاز نشده است.

- Audit snapshot: `2bf75e834d0444ac5cfef767261757324cbefee8` on `audit/full-source-review`
- Documentation branch: `rebuild/blueprint-v1` created from the exact audit commit
- Blueprint commit: 95b901d9a4bccb5d96f50c256668c2d2a904423c
- Rebuild status: **IN PROGRESS**
- Last accepted phase: **01 — Core Foundation and Unified Orchestration**
- Active phase: **NONE**
- Phase 01 commit: 0465ff2d88fdd90fc5a1039ecf86cc698c9022d1
- Phase 01 acceptance-fix commit: 8e292a70d20bc11450f41a03de175b81ef149356
- Phase 01 final hardening commit: FINAL_HARDENING_COMMIT_PLACEHOLDER
- Next allowed action: independent re-review of the final hardening; Phase 02 is NOT authorized to start.

## Audit status and limitations
- Audit source state: local `~/AI-Factory` HEAD `3878db271313415656328ec57595d1fe87c5fa3a`, branch `main`, 151 uncommitted items, zero remotes, version `0.47.0`.
- Audit snapshot has 256 tracked files and 368 SHA256SUMS entries; 112 checksum entries do not match tracked files (mostly bytecode) and one tracked file lacks a checksum. This is an open audit finding, not repaired here.
- Test report: 221 PASS, 1 FAIL, 0 ERROR; 222 tests executed; failing test `test_root_isolated_when_env_set`. The suite is NOT fully green.
- Live provider, model, service and payment paths: NOT TESTED.
- Real browser screenshot inspection: BLOCKED by security policy; no fabricated screenshot.
- Not all specialists are independently validated; professional image/audio output is not proven.

## Phase 01 evidence (real)
- Branch: `rebuild/phase-01-core-foundation`, based on Blueprint commit `61240a7f35fb49586e4d739d8f9228ee86bc025a`.
- `oc_state()` now delegates to `gateway().state()`; verified by a non-mocked injected gateway test.
- Canonical event envelope shared by ToolEvent and AgentEvent ledgers (`core/event_envelope.py`).
- Durable ToolEvent receipts make duplicate `call_id` replay FAIL-CLOSED without re-executing the adapter.
- Workflow stages persist a run lease (`run_pid`, `run_token`); stale RUNNING stages after a crash are reopened as REVISION with recorded history, no artifact/version loss.
- Cancellation requests are durably observable via `tool_executor.state()` and `tool_events.find("cancel-"+call_id)`.
- ModelGateway boundary has opt-in strict conformance validation; existing permissive `set_gateway` behavior is preserved.
- Targeted acceptance fixes: atomic call-id claim under `_seen_lock`; durable idempotency receipts in `data/tool_receipts.json` separated from bounded display history. No destructive migration and no data loss.
- Final hardening: RESERVED/IN_PROGRESS receipt is persisted under `_seen_lock` BEFORE any adapter runs; terminal outcome is finalized after execution; unresolved receipts fail closed and require explicit `reconcile(call_id, status, actor, reason)` — never an automatic rerun. `_load_receipts()` distinguishes initialized-empty from missing/corrupt/unreadable via a marker and schema validation; corruption is never treated as zero prior calls and is never overwritten.
- Accurate guarantee: at-most-once execution claims (no double adapter start, even after restart). Exactly-once external side effects are NOT claimed where the adapter/provider lacks transactional idempotency; a crash after a side effect but before the terminal receipt leaves the call unresolved for explicit reconciliation.
- Tests after final hardening: `Ran 239 tests in 42.861s — OK` (239 PASS, 0 FAIL, 0 ERROR) with `AI_FACTORY_TEST_ROOT` isolated. Concurrency used `threading.Barrier`/`Event`; crashes simulated by direct lifecycle calls and injected faults; no timing sleeps. See `docs/rebuild/evidence/PHASE-01-TESTS.md`.
- The historical `test_root_isolated_when_env_set` hardcoded-path failure was within Phase 01 state-isolation scope and was corrected in the isolated rebuild clone; it is not hidden.

## Open blockers
1. Checksum mismatch and stale test-status contradiction remain recorded until a separate audit-maintenance change.
2. Canonical specialist registry and workflow registry are inconsistent (Phase 04).
3. No authenticated live provider capability verification (Phase 06/09).
4. Public onboarding lacks license/CI/setup verification (Phase 10).
5. ADR-011..014 remain UNRESOLVED; no later phase may silently decide them.
6. Receipts for call_ids already evicted from the old 600-event history before this fix cannot be reconstructed; no destructive migration was performed. This is a historical audit limitation, not a regression of the new mechanism.

## Next action
The final hardening is ready for independent review. Do NOT mark Phase 01 COMPLETE until independent review accepts that both fail-open cases (crash window and receipt-store corruption/unavailability) are closed. Do NOT start Phase 02 automatically; it requires a new explicit user authorization after independent acceptance.
