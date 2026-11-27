# Current State

> خلاصه فارسی: فاز ۰۱ توسط بازبینی مستقل پذیرفته شد (ACCEPTED). فاز ۰۲real اجرا شد: لایهٔcanonical هوش درخواست، طبقهبندی فارسی/مختلط، تفکیک حوزه و پلتفرم، clarification ضروری، خطر/پیچیدگی جدا، برنامهٔ محدود و تأیید امن گرهخورده ساخته و آزمایش شد. فاز ۰۲ منتظر بازبینی مستقل است و **پذیرفته نشده است**؛ فاز ۰۳ مجاز نیست.

- Audit snapshot: `2bf75e834d0444ac5cfef767261757324cbefee8` on `audit/full-source-review`
- Documentation branch: `rebuild/blueprint-v1` created from the exact audit commit
- Blueprint commit: 95b901d9a4bccb5d96f50c256668c2d2a904423c
- Rebuild status: **IN PROGRESS**
- Last accepted phase: **01 — Core Foundation and Unified Orchestration** — ACCEPTED by independent review dated 2026-11-27 (prior provisional status recorded historically below was superseded by that decision).
- Phase 01 status: **ACCEPTED**. Basis: final code commit `43bf30818e77b47b4ce23e70bd35738a0eb1db71`, documentation/remote HEAD commit `3dcae0703bee408bc34fdfd83e81807a25c6a2a7`, independent review 2026-11-27.
- Active phase: **02 — Request Intelligence & Planning** (implementation complete; awaiting independent review).
- Phase 02 status: **NOT ACCEPTED — READY FOR INDEPENDENT REVIEW** (critical corrections applied after independent review findings; see below).
- Phase 03 status: **NOT AUTHORIZED** — requires explicit user authorization after independent Phase 02 acceptance.
- Phase 01 commit: 0465ff2d88fdd90fc5a1039ecf86cc698c9022d1
- Phase 01 acceptance-fix commit: 8e292a70d20bc11450f41a03de175b81ef149356
- Phase 01 final hardening commit: e026891a4f9de0d6d3c3e8f1d6a64aa2f170355b
- Phase 01 multi-process fix commit: d1e801e0e9e63ab9eeda71393b92f746f9b56c39
- Phase 01 reconciliation atomicity commit: 43bf30818e77b47b4ce23e70bd35738a0eb1db71
- Historical note (preserved, not rewritten): before the independent decision, this file recorded `Last accepted phase: NONE` and Phase 01 as awaiting acceptance. That status is superseded by the 2026-11-27 independent acceptance recorded above.
- Next allowed action: independent review of Phase 02 on `rebuild/phase-02-request-intelligence`. Do not start Phase 03 automatically.

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
- Multi-process fix: `fcntl.flock` on a stable `data/tool_receipts.json.lock` inside one `_receipt_lock()` context manager shared by threads and processes; `claim()` does read → duplicate check → signature validation → durable RESERVED write atomically, and the adapter runs only for the winning claimant. RESERVED/IN_PROGRESS/TERMINAL all use that lock; writers use unique `pid.thread.random.tmp` files with atomic `replace`. Lock/read/write/corruption failures fail closed.
- Reconciliation audit trail: `reconcile()` performs validate → load → reject → persist inside ONE `_receipt_lock()` critical section, rejecting anonymous actors, non-terminal statuses, missing and already-terminal receipts; the competing reconciler gets an explicit `already-reconciled` error while first-terminal-wins is preserved. `reconciled_by`, `reconcile_reason` and `reconciled_at` are persisted in the terminal receipt; no automatic rerun.
- Exact multi-process guarantee: on local POSIX filesystems supporting `flock`, independent processes cannot both win the claim for one `call_id` — at-most-once execution claims hold across processes and restarts. LIMITS: exactly-once external side effects are NOT claimed; `flock` is advisory (foreign writers bypass it); without `fcntl` the module fails closed and narrows to single-process safety; historical evicted receipts cannot be reconstructed.
- Tests after reconciliation atomicity: `Ran 247 tests in 43.440s — OK` (247 PASS, 0 FAIL, 0 ERROR, exit 0) with `AI_FACTORY_TEST_ROOT` isolated; targeted `Ran 20 tests in 4.919s — OK` (exit 0). Real child processes synchronised with `multiprocessing.Barrier`; the competing-reconciler race passed 5/5 stress reruns; no timing sleeps. Exact commands are reported in the final delivery message.
- The historical `test_root_isolated_when_env_set` hardcoded-path failure was within Phase 01 state-isolation scope and was corrected in the isolated rebuild clone; it is not hidden.

## Phase 02 evidence (real, implementation on `rebuild/phase-02-request-intelligence`)
- Base: accepted Phase 01 SHA `3dcae0703bee408bc34fdfd83e81807a25c6a2a7`; branch created from that exact commit, clean tree, independent clone only.
- `core/request_intelligence.py` — one canonical versioned request record (`af-request-1.0`), flock-guarded atomic persistence, schema validation and fail-closed corrupt/missing store handling; no competing request registry.
- Deterministic Persian/mixed-language intent extraction (create/modify/debug/explain/analyze/plan/research/deploy/other) reusing `core.requestcheck` for the legacy product-type hint; no provider/model call and no new dependency.
- Product domain and delivery platform are separate fields and separate classification functions; game≠Android and Android≠game; unknown and multi-platform cases supported; ADR-011 untouched.
- Explicit/inferred/system requirements separated with rationale/confidence; material conflicts recorded; acceptance conditions present; unknown estimates stay unknown.
- Essential-only clarification with one compact question, no repeated questions, revision increment on every answer, legal transition validation.
- Risk (9 dimensions → low/medium/high/critical with reasons) is independent of complexity (small/medium/large/very-large/unknown); low-risk controlled autonomy is bounded; high-risk requires exact explicit approval.
- Approval bound to request ID + revision + request hash + plan hash + scope + actor + one-shot expiring token; changed request/plan, replay, mismatch and expiry all fail closed; SecurityPolicy never bypassed.
- Bounded plan (≤8 structured steps, deduplicated, dependency-valid, `executed: False`); no specialist/tool/provider execution in Phase 02.
- Idempotent non-destructive legacy migration with preserved unknown fields and `legacy` rollback mode that does not delete canonical records.
- API: `/api/requests`, `/api/requests/view`, `/api/requests/intake`, `/api/requests/answer`, `/api/requests/plan`, `/api/requests/approval-token`, `/api/requests/approve`; responses strip approval tokens. Workspace gained a small read-only summary card; visual inspection NOT TESTED.
- Tests: baseline `Ran 247 tests ... OK`; targeted Phase 02 `Ran 58 tests in 0.237s — OK` (exit 0); full suite `Ran 305 tests in 47.713s — OK` (305 PASS / 0 FAIL / 0 ERROR, exit 0), isolated `AI_FACTORY_TEST_ROOT`. Details: `docs/rebuild/evidence/PHASE-02-REQUEST-INTELLIGENCE.md`.

## Phase 02 critical corrections (after independent review findings, on the same branch)
- Answer handling: `answer()` now validates each answer (`resolve_answer`), stores it separately with `question_id`, answer text, timestamp, new revision, provenance `user-clarification`, resolved flag and canonical value; preserves `raw_text`; recomputes intent/domain/platform, requirements, constraints, assumptions, conflicts, risk, complexity, autonomy eligibility and clarification from the effective original text + answers; unresolved/invalid answers never clear a question; a material answer invalidates the stale plan and approval; the request hash is recomputed after all mutations.
- No automatic approval: `build_plan()` leaves a low-risk plan at `PLANNED` with `approval.status = NOT_REQUIRED`, no actor and no `APPROVED` state; no system actor impersonates user consent. A separate `can_execute()` boundary check enforces approvals at execution time.
- Real execution boundary: `orchestrator.canonical_execution_gate()` requires successful canonical intake in canonical mode; intake/corruption/unavailability failures, `BLOCKED`, `REJECTED`, `NEEDS_CLARIFICATION` and `AWAITING_APPROVAL` all stop the task before any model/specialist execution. Legacy mode is an explicit documented switch and never activates after a canonical failure. Deterministic task IDs prevent duplicate canonical records and revalidate approval on every retry.
- Approval integrity: proposal and consume run inside one flock-guarded critical section; token binding checks request ID, revision, request hash, plan hash and scope; expiry uses the authoritative confirmation-store timestamp; a trusted panel session is required when a session is supplied, otherwise the actor string is recorded but explicitly NOT claimed as authenticated; API views never expose the token.
- Risk correction: fixed the malformed public-release pattern and added honest public release/publish/deploy, paid, credentials, private data, network, destructive and migration cases; no risk level was lowered to pass tests; vague high-impact operations are not treated as low risk.
- Tests: targeted Phase 02 suites `Ran 79 tests ... OK` (exit 0); full suite `Ran 326 tests in 49.636s — OK` (326 PASS / 0 FAIL / 0 ERROR, exit 0), isolated `AI_FACTORY_TEST_ROOT`. Details: `docs/rebuild/evidence/PHASE-02-REQUEST-INTELLIGENCE.md`.

## Open blockers
1. Checksum mismatch and stale test-status contradiction remain recorded until a separate audit-maintenance change.
2. Canonical specialist registry and workflow registry are inconsistent (Phase 04).
3. No authenticated live provider capability verification (Phase 06/09).
4. Public onboarding lacks license/CI/setup verification (Phase 10).
5. ADR-011..014 remain UNRESOLVED; no later phase may silently decide them.
6. Receipts for call_ids already evicted from the old 600-event history before this fix cannot be reconstructed; no destructive migration was performed. This is a historical audit limitation, not a regression of the new mechanism.

## Next action
Phase 02 is **READY FOR INDEPENDENT REVIEW** and is NOT accepted or COMPLETE until that review. **Phase 03 has NOT started and is NOT authorized.** Wait for explicit user authorization after independent Phase 02 acceptance. Historical provisional Phase 01 status entries are preserved above, not deleted.
