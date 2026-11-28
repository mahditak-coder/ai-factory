# Current State

> خلاصه فارسی: فاز ۰۱ و فاز ۰۲ توسط بازبینی مستقل پذیرفته شدند (ACCEPTED). فاز ۰۳real اجرا شد: موتور تحقیق با provenance، مقایسهٔ گزینهها، ارزیابی امکانپذیری و Product Brief نسخهدار با معیارهای پذیرش ساخته و آزمایش شد. فاز ۰۳ **NOT ACCEPTED — READY FOR INDEPENDENT REVIEW** است؛ فاز ۰۴ مجاز نیست.

- Audit snapshot: `2bf75e834d0444ac5cfef767261757324cbefee8` on `audit/full-source-review`
- Documentation branch: `rebuild/blueprint-v1` created from the exact audit commit
- Blueprint commit: 95b901d9a4bccb5d96f50c256668c2d2a904423c
- Rebuild status: **IN PROGRESS**
- Last accepted phase: **02 — Request Intelligence & Planning** — ACCEPTED by final independent review dated 2026-11-28.
- Phase 01 status: **ACCEPTED**. Basis: final code commit `43bf30818e77b47b4ce23e70bd35738a0eb1db71`, documentation/remote HEAD commit `3dcae0703bee408bc34fdfd83e81807a25c6a2a7`, independent review 2026-11-27.
- Phase 02 status: **ACCEPTED** (final independent review 2026-11-28). Acceptance basis: final code/documentation commit `075921b5f0b8ebe7638b0d38c6394c6402c94541` on `rebuild/phase-02-request-intelligence`, with the full scope of Phase 02 corrections — durable canonical request model, answer mutation, no auto-approval, fail-closed execution gate, genuine one-shot capability, strict session/project binding and authoritative panel-principal attribution. Earlier `NOT ACCEPTED` entries below are historical and SUPERSEDED by this decision; they are not deleted or rewritten.
- Active phase: **03 — Research, Innovation & Product Strategy** (implementation complete; awaiting independent review).
- Phase 03 status: **NOT ACCEPTED — READY FOR FINAL INDEPENDENT REVIEW**.
- Phase 04 status: **NOT AUTHORIZED** — requires explicit user authorization after independent Phase 03 acceptance.
- Phase 03 base: created from the exact accepted Phase 02 commit `075921b5f0b8ebe7638b0d38c6394c6402c94541`, not from `main` or any unrelated branch.
- Phase 01 commit: 0465ff2d88fdd90fc5a1039ecf86cc698c9022d1
- Phase 01 acceptance-fix commit: 8e292a70d20bc11450f41a03de175b81ef149356
- Phase 01 final hardening commit: e026891a4f9de0d6d3c3e8f1d6a64aa2f170355b
- Phase 01 multi-process fix commit: d1e801e0e9e63ab9eeda71393b92f746f9b56c39
- Phase 01 reconciliation atomicity commit: 43bf30818e77b47b4ce23e70bd35738a0eb1db71
- Historical note (preserved, not rewritten): before the independent decision, this file recorded `Last accepted phase: NONE` and Phase 01 as awaiting acceptance. That status is superseded by the 2026-11-27 independent acceptance recorded above.
- Historical note (preserved): Phase 02 was recorded as `NOT ACCEPTED — READY FOR INDEPENDENT REVIEW` before the final independent decision; that status is SUPERSEDED by the 2026-11-28 acceptance above.
- Next allowed action: independent review of Phase 03 on `rebuild/phase-03-research-product-strategy`. Do not start Phase 04 automatically.

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
- No automatic approval: `build_plan()` leaves a low-risk plan at `PLANNED` with `approval.status = NOT_REQUIRED`, no actor and no `APPROVED` state; no system actor impersonates user consent. `NOT_REQUIRED` is not execution permission; `consume_execution()` is the only execution-edge check, and `can_execute()` is read-only.
- Real execution boundary: `orchestrator.canonical_execution_gate()` requires successful canonical intake in canonical mode; intake/corruption/unavailability failures, `BLOCKED`, `REJECTED`, `NEEDS_CLARIFICATION` and `AWAITING_APPROVAL` all stop the task before any model/specialist execution. Legacy mode is an explicit documented switch and never activates after a canonical failure. Deterministic task IDs prevent duplicate canonical records and revalidate approval on every retry.
- Approval integrity: proposal and consume run inside one flock-guarded critical section; token binding checks request ID, revision, request hash, plan hash and scope; expiry uses the authoritative confirmation-store timestamp; a trusted panel session is required when a session is supplied, otherwise the actor string is recorded but explicitly NOT claimed as authenticated; API views never expose the token.
- Risk correction: fixed the malformed public-release pattern and added honest public release/publish/deploy, paid, credentials, private data, network, destructive and migration cases; no risk level was lowered to pass tests; vague high-impact operations are not treated as low risk.
- Tests: targeted Phase 02 suites `Ran 79 tests ... OK` (exit 0); full suite `Ran 326 tests in 49.636s — OK` (326 PASS / 0 FAIL / 0 ERROR, exit 0), isolated `AI_FACTORY_TEST_ROOT`. Details: `docs/rebuild/evidence/PHASE-02-REQUEST-INTELLIGENCE.md`.

## Phase 02 final execution-boundary corrections (same branch)
- Removed implicit authorization: `can_execute()` never grants execution from `CLASSIFIED`, `READY_FOR_PLAN`, `PLANNED` or `NOT_REQUIRED`. A separately defined, explicit `authorize_execution()` issues a one-shot capability bound to request ID, revision, request hash, plan hash, exact step-list hash, project, goal hash, scope, actor and expiry. High/critical work additionally requires the existing explicit user approval. No system actor or system-generated state is treated as user consent.
- Exact approved scope: execution authorization is checked against the actual project, goal and exact plan steps at the boundary; stale plans, changed goals/projects, extra or modified steps, missing plan binding and unsupported execution scopes fail closed. Phase 02 cannot enforce a bounded plan for the unrestricted legacy Task pipeline, so canonical mode fails closed there; the handoff contract for a later authorized phase is documented in the Phase 02 evidence file.
- Pipeline bypass closed: `canonical_execution_gate()` now runs at the very top of `orchestrator._execute()` before the pipeline early-return, and also at `pipeline.execute()` and the direct `POST /api/factory/generate` entry point. Canonical intake failure, corrupt/missing store, clarification-pending, blocked/rejected, approval-pending and stale authorization all stop pipeline and ordinary tasks before any model/specialist/tool side effect. Explicit legacy compatibility switch remains documented and cannot activate on a canonical error.
- True one-shot execution authorization: `propose_execution()` → `confirm_execution()` → atomic `consume_execution()`; state `PROPOSED` → `AUTHORIZED` → `CONSUMED`; consumption happens under the canonical lock before any side effect, is single-use across processes and survives reload. A genuine confirmation token establishes `consent_confirmed=True`; `actor_trusted=True` only after a validated panel session bound to the original proposal. A bare actor string is never trusted. Session bindings cannot be bypassed with an empty/different session, and the execution project must equal the canonical request's own project at proposal, confirmation and consumption. Read-only `can_execute()` is never permission.
- Session/ownership/consistency corrections: proposal session binding is read from the original confirmation record and enforced strictly (missing, different, invalid or expired sessions rejected); anonymous proposals cannot be upgraded by an arbitrary session string; `actor_trusted` requires a bound authenticated panel session; project ownership is checked against `rec["project"]` at all three steps; API-safe views expose no token or session. Canonical mode FAILS CLOSED when no exact bounded adapter/plan is supplied; no unrestricted legacy execution continues in canonical mode.
- Identity attribution final: `panelauth.principal_for_session()` resolves the authoritative principal (`panel_admin`) from the server-side session; a caller-supplied actor label is never authenticated by mere session validity. `authenticated_principal`, `declared_actor`, `consent_confirmed` and `actor_trusted` are recorded separately; API views expose no session/token. A latent workflow `_ACTIVE` race was fixed atomically so the existing concurrency regression stays green.
- Tests: targeted Phase 02 suites `Ran 133 tests in 0.935s — OK` (exit 0); full suite `Ran 380 tests in 43.166s — OK` (380 PASS / 0 FAIL / 0 ERROR, exit 0), isolated `AI_FACTORY_TEST_ROOT`; real two-process replay proved exactly one consumption. Legacy orchestrator tests explicitly opt into the documented legacy compatibility switch; no test was deleted or weakened.
- CI/GitHub Actions and browser/viewport visual inspection: NOT TESTED (no real run available).

## Phase 03 evidence (real, on `rebuild/phase-03-research-product-strategy`)
- Base: accepted Phase 02 commit `075921b5f0b8ebe7638b0d38c6394c6402c94541`; isolated clone only, clean starting tree.
- `core/research_product.py` — one canonical Research & Product Strategy store with flock-guarded atomic writes, marker/schema validation and fail-closed corrupt/missing storage. No parallel registry.
- Research Engine: `add_source()` records URL, title, claim, claim type (`verified_fact`/`hypothesis`/`estimate`/`unknown`), access time, provenance (researcher/method/hash), and idempotent source IDs; untrusted source text is stored as DATA only. `assess_sources()` detects insufficient, unverified and contradictory evidence.
- Paid research: `propose_paid_research()` requires an explicit confirmation token; `run_paid_research()` fails closed and performs NO paid side effect in Phase 03 (`BLOCKED`, `paid_executed=False`).
- Alternatives: transparent scoring over cost/complexity/risk/time/feasibility, recorded pros/cons/limits, evidence links, explicit rejection reasons, and blocked auto-selection when evidence or scores are insufficient.
- Feasibility: technical/operational/economic/practical dimensions evaluated independently; unknown estimates stay unknown; dependencies, risks, environment limits and `requires_human_review` recorded; no fabricated prices, market statistics or provider capability.
- Product Brief: versioned record with stable ID, request link, schema version, status, timestamps, hash, immutable previous versions in `brief_history`, real artifact file, and acceptance criteria validation (measurable/testable/linked/unknowns). Final scope/success acceptance is reserved to the user via a bound confirmation token; the system never marks its own brief `FINAL/ACCEPTED`.
- Real artifacts: `docs/product/brief-v1.json` (file SHA-256 `33203151beaff9058f5ea6620bf4f0c11aeb50e18feb8b6d13f7c805e2cdbd`, brief hash `efc67e79d899058b3a22e4ca79a709bf15bf4eeb9068b3ef7dc122e257db7109`) and `docs/product/acceptance-criteria-v1.json` with explicit unknowns `ac-1:status`, `ac-2:status`.
- Audit repair on the same branch: caller-declared `claimed_type` is now separate from system-controlled `verification_status` (always `unverified` in Phase 03 — no trusted verifier); `.invalid`/unreachable/unchecked sources can never make evidence sufficient; forged provenance/verification fields are rejected; auto-selection stays fail-closed. Brief approval now re-reads the current record and validates ID/project/hash/status/token/session and consumes the token inside ONE `_store_lock()` critical section; actor trust follows the Phase 02 panel-principal pattern. `write_brief_artifact()` validates an allowlisted project ID, resolves and checks containment BEFORE any mkdir/write, and rejects symlink escape. Option scores are explicitly `*_utility` (0..5, higher = more desirable; never raw cost/risk/time amounts).
- Final minimal regression fix: the utility-criterion loop variable in `add_alternative()` no longer shadows the option name; a two-option regression test verifies names in storage and in `compare_alternatives()`. No security or lifecycle behavior changed.
- Tests after final fix: targeted Phase 03 `Ran 46 tests ... OK` (exit 0); full suite `Ran 426 tests in 45.723s — OK` (426 PASS / 0 FAIL / 0 ERROR, exit 0), isolated `AI_FACTORY_TEST_ROOT`. Details: `docs/rebuild/evidence/PHASE-03-RESEARCH-PRODUCT-STRATEGY.md`.
- CI/GitHub Actions, live providers, payments and browser/viewport visual inspection: NOT TESTED (no real run available). Product scope and success metrics are awaiting USER approval.

## Open blockers
1. Checksum mismatch and stale test-status contradiction remain recorded until a separate audit-maintenance change.
2. Canonical specialist registry and workflow registry are inconsistent (Phase 04).
3. No authenticated live provider capability verification (Phase 06/09).
4. Public onboarding lacks license/CI/setup verification (Phase 10).
5. ADR-011..014 remain UNRESOLVED; no later phase may silently decide them.
6. Receipts for call_ids already evicted from the old 600-event history before this fix cannot be reconstructed; no destructive migration was performed. This is a historical audit limitation, not a regression of the new mechanism.

## Next action
Phase 03 is **NOT ACCEPTED — READY FOR INDEPENDENT REVIEW**, and its Product Brief/success criteria await explicit user approval. **Phase 04 has NOT started and is NOT authorized.** Historical provisional/superseded status entries are preserved above, not deleted.
