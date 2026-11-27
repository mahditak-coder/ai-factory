# Phase 02 — Request Intelligence & Planning: implementation and evidence

> خلاصه فارسی: لایهٔcanonical هوش درخواست ساخته شد. متن کاربر بدون اجرای متخصص به یک رکورد نسخهدار، طبقهبندی شده، قابل بازبینی و دارای برنامهٔ محدود تبدیل میشود. تأیید حساس با.Hash/نسخه/توکن تکبارهای گره خورده و در هر عدم تطابق fail-closed است.

Base: `rebuild/phase-01-core-foundation` @ `3dcae0703bee408bc34fdfd83e81807a25c6a2a7` (Phase 01 ACCEPTED by independent review 2026-11-27, code commit `43bf30818e77b47b4ce23e70bd35738a0eb1db71`, docs commit `3dcae0703bee408bc34fdfd83e81807a25c6a2a7`).

## What was implemented (real source)

| Deliverable | Implementation |
|---|---|
| A — canonical request model | `core/request_intelligence.py`: `SCHEMA_VERSION = af-request-1.0`, `validate_record()`, `intake()`, `get()`, `list_requests()`, persisted in `data/canonical_requests.json` with flock-guarded atomic writes and a marker distinguishing genuine empty from lost/corrupt store. Fields: identity/lifecycle, intent, requirements, constraints/assumptions, classification, clarification, plan, approval, legacy/migration. |
| B — intent extraction | Deterministic regex/scoring over normalized Persian (Arabic/Persian character and digit unification) and mixed Persian-English text. Intents: create, modify, debug, explain, analyze, plan, research, deploy, other. Reuses `core.requestcheck.analyze()` for the legacy product-type hint; no provider/model call and no new dependency. Low confidence is recorded as `uncertain` and drives clarification. |
| C — independent domain/platform | `classify_domain()` and `classify_platform()` are separate functions and separate record fields. Domain values: game, education, business, automation, content, general, unknown. Platform values: Android, Web, Desktop, Cross-platform, unknown (+ `platforms` list for multi-platform). A game does not imply Android; Android does not imply game. ADR-011 is untouched. |
| D — requirement extraction | `extract_requirements()` separates `explicit`, `inferred` (with `confidence` + `rationale`) and `system` requirements; `detect_conflicts()` records material contradictions (e.g. offline + live online) instead of silently choosing. Acceptance-condition fields are present; unknown targets stay unknown. |
| E — essential clarification | `clarification_state()` asks only for information that changes the safe/useful result. One concise question per turn; optional preferences are recorded without blocking; answered keys (`question_id`) are never re-asked. `answer()` always increments `revision` and invalidates prior approval. Legal states and transitions are validated by `_transition()`. |
| F — risk & complexity | `classify_risk()` evaluates nine separate dimensions (destructive, external_release, financial, security_sensitive, credentials, private_data, scope_expansion, network, data_loss_migration) and maps them by documented rules to low/medium/high/critical with reasons. `classify_complexity()` is independent (small/medium/large/very-large/unknown). Estimates are always `unknown` unless the user supplied real numbers. |
| G — autonomy & approval | (Historical statement superseded by later corrections:) low-risk plans are NOT approved; they remain `PLANNED` with `approval.status=NOT_REQUIRED` and no actor. Sensitive/irreversible requests require `approval_proposal()` + `approve()`; approval is bound to request ID, exact revision, request hash, plan hash, scope, actor and a one-shot `core.confirmations` token with expiry. Any material change invalidates it; replay/mismatch/expiry fails closed. SecurityPolicy is never bypassed. Execution requires the separate three-step capability described below, never `NOT_REQUIRED` alone. |
| H — bounded plan | `build_plan()` generates at most eight structured steps (id, purpose, required_input, expected_output, dependencies, acceptance_condition, risk, approval_required, status), deduplicates step IDs, marks `executed: False`, and performs no tool/specialist/provider call. |
| I — legacy migration | `migrate_legacy()` is idempotent via `legacy.legacy_ref`, preserves unknown legacy fields under `legacy.preserved`, records source/version/timestamp, never deletes legacy data. `compat_mode()`/`set_compat_mode("legacy")` provide rollback without deleting canonical records. |
| J — orchestrator/API | Non-breaking adapter in `core/orchestrator.py` creates a canonical record per task in canonical mode; failure never stops the Phase 01 path. New API routes: `GET /api/requests`, `/api/requests/view`, `POST /api/requests/intake`, `/api/requests/answer`, `/api/requests/plan`, `/api/requests/approval-token`, `/api/requests/approve`. Responses use `save_view()` which strips approval tokens. |
| K — Workspace | `dashboard/workspace.html` gained a small read-only card showing understood intent, domain, platform, risk, complexity, revision and the single next action. No new approval panel; approvals remain in the existing Decision Center. Visual quality NOT TESTED (no browser/viewport inspection available). |

## Exact verification evidence

All commands ran from the isolated clone with a fresh `AI_FACTORY_TEST_ROOT`; no original source or user project data was touched.

1. Baseline before changes (branch at accepted Phase 01 SHA):
   - `python3 -m unittest discover -s core/tests -p 'test_*.py'`
   - exit `0`, `Ran 247 tests in 45.481s — OK`; log `/data/data/com.termux/files/usr/tmp/opencode/p2-baseline.log`.
2. Targeted Phase 02 suite:
   - `python3 -m unittest core.tests.test_phase02_request_intelligence -v`
   - exit `0`, `Ran 58 tests in 0.237s — OK`; log `/data/data/com.termux/files/usr/tmp/opencode/p2-target6.log`.
3. Full suite after implementation:
   - `python3 -m unittest discover -s core/tests -p 'test_*.py'`
   - exit `0`, `Ran 305 tests in 47.713s — OK` (305 PASS / 0 FAIL / 0 ERROR); log `/data/data/com.termux/files/usr/tmp/opencode/p2-full.log`.
   - A pre-existing non-fatal `ResourceWarning` (unclosed `opencode-serve.log` writer) appears after the OK line.
4. Final rerun after all code/document changes: `python3 -m unittest discover -s core/tests -p 'test_*.py'` — exit `0`, `Ran 305 tests in 46.320s — OK`; log `/data/data/com.termux/files/usr/tmp/opencode/p2-final.log`.
5. `python3 -m py_compile` on all changed Python files: exit `0`.

Test coverage includes all 50 mission cases: model roundtrip/invalid schema/stable identity/revision-history/legacy preservation; Persian and mixed-language intent; create/modify/debug/explain; explicit vs inferred; uncertainty; conflicts; game-Android, game-Web, education-Android, website-Desktop/Web, unknown platform, domain/platform independence; essential clarification, no over-asking, no repeated questions, revision update, persistence; destructive/paid/private risk, complex-but-safe, simple-destructive-not-safe, unknown estimates; bounded/dependency-valid/acceptance-condition/duplicate-free/non-executing plans; approval need, high-risk gating, request-hash/plan-hash binding, invalidation on request/plan change, one-shot replay rejection, expiry/mismatch, untrusted text cannot override policy; idempotent legacy migration, preserved legacy fields, duplicate submission, Phase 01 receipt regression, API state exposure, invalid transitions, reload persistence; plus corrupt/missing store fail-closed, rollback compat mode, anonymous approval rejection and token hiding in API views.

## Critical corrections after independent review (same branch)

| Defect | Fix | Evidence |
|---|---|---|
| Clarification answers did not mutate the canonical request | `resolve_answer()` validates/maps answers; `apply_answers()` recomputes intent/domain/platform, requirements, constraints, assumptions, conflicts, risk, complexity, autonomy and clarification from original text + answers; `raw_text` preserved, answers stored separately with `question_id`, timestamp, revision, provenance and resolved flag; unresolved answers never clear a question; stale plan/approval invalidated; hash recomputed last | `core/tests/test_phase02_corrections.py` (`test_answer_*`, `test_invalid_answers_*`, `test_request_hash_*`, `test_material_answer_*`) |
| `build_plan()` fabricated `APPROVED` by `system:low-risk-autonomy` | Low/medium-risk plans stay `PLANNED` with `approval.status=NOT_REQUIRED`, empty actor, no `APPROVED` transition; execution is enforced separately by atomic `consume_execution()` at the boundary; read-only `can_execute()` is never permission | `test_low_risk_planning_never_approves`, `test_no_fabricated_human_approval_*` |
| Orchestrator swallowed canonical failures and executed anyway | `canonical_execution_gate()` fails closed on intake/corruption/unavailability and on `BLOCKED`/`REJECTED`/`NEEDS_CLARIFICATION`/`AWAITING_APPROVAL`; legacy mode only via explicit switch; retries revalidate via deterministic task-scoped request IDs | `test_intake_failure_prevents_model_execution`, `test_corrupt_or_missing_store_*`, `test_non_ready_statuses_*`, `test_legacy_mode_is_explicit_*` |
| Approval races / misleading expiry / unauthenticated actor | Proposal and consume run under one `_request_lock()`; exact binding to ID/revision/hash/plan/scope; authoritative absolute expiry from confirmation store; trusted panel session required when supplied; actor otherwise recorded but explicitly not claimed authenticated; token stripped from API views | `test_concurrent_approval_one_winner`, `test_concurrent_answer_and_approval_*`, `test_token_never_exposed_*`, `test_replayed_stale_*` |
| Risk classifier typo / nine dimensions | Fixed `external_release` pattern (`عمومی|public`), corrected `api key` spacing, added vague high-impact guard; nine dimensions verified, no level lowered | `test_all_nine_risk_dimensions_honest`, `test_unknown_high_impact_is_not_low` |

### Correction verification commands
1. `python3 -m unittest core.tests.test_phase02_corrections -v` — exit `0`, `Ran 21 tests ... OK`; log `/data/data/com.termux/files/usr/tmp/opencode/p2fix-t4.log`.
2. `python3 -m unittest core.tests.test_phase02_request_intelligence core.tests.test_phase02_corrections` — exit `0`, `Ran 79 tests in 0.391s — OK`; log `/data/data/com.termux/files/usr/tmp/opencode/p2fix-target.log`.
3. `python3 -m unittest discover -s core/tests -p 'test_*.py'` — exit `0`, `Ran 326 tests in 49.636s — OK` (326 PASS / 0 FAIL / 0 ERROR); log `/data/data/com.termux/files/usr/tmp/opencode/p2fix-full2.log`.
4. All runs used a fresh isolated `AI_FACTORY_TEST_ROOT`. Existing Phase 01/Phase 02 tests were preserved; the one outdated expectation (low-risk `APPROVED`) was corrected to the newly required `PLANNED`/`NOT_REQUIRED` behavior, not hidden.

### Honest limitations after corrections
- Actor identity is **self-declared unless a trusted panel session is supplied**; the code records `actor_trusted` and does not claim authenticated identity otherwise.
- Phase 02 still executes no bounded action; `consume_execution()` is the one-shot execution boundary, and `can_execute()` is read-only. Approval alone never permits execution.
- CI/GitHub Actions: **NOT TESTED** — no actual workflow run was performed.
- Browser/mobile visual inspection: **NOT TESTED** — no real viewport/visual review was available.
- Model-assisted classification, live providers, paid actions, specialist execution and product generation remain out of scope / NOT TESTED.
- ADR-011..014 remain untouched and unresolved.

## Final execution-boundary corrections (third independent-review round)

| Defect | Fix | Evidence |
|---|---|---|
| Implicit execution authorization for low/medium risk | `can_execute()` now requires an explicit `execution` capability for EVERY risk level; that capability is issued only through the three-step model below (propose → explicit confirmation → atomic consumption) and is bound to request ID, revision, request hash, plan hash, exact step-list hash, project, goal hash, scope, actor, session and authoritative expiry; high/critical additionally require explicit user approval; no system-generated state is consent | `core/tests/test_phase02_execution_boundary.py` (`test_low_risk_*`, `test_medium_risk_*`, `test_high_critical_*`, `test_plan_ready_*`) |
| Approval not bound to the actually executed plan | Boundary checks exact project, goal, scope, plan hash and exact step list; extra/modified steps, changed goal/project, stale plan, missing plan and unsupported scope fail closed | `test_approval_cannot_authorize_a_different_plan`, `test_approval_cannot_authorize_another_project`, `test_approval_cannot_authorize_changed_goal`, `test_extra_or_modified_steps_rejected`, `test_valid_approval_does_not_authorize_unrestricted_legacy_execution`, `test_unsupported_execution_adapter_fails_closed` |
| Pipeline early-return bypass | Gate moved to the top of `orchestrator._execute()` before the `kind=="pipeline"` branch; also added at `pipeline.execute()` and `POST /api/factory/generate`; canonical failures never activate legacy mode; explicit legacy switch preserved | `test_pipeline_blocked_when_intake_fails`, `test_pipeline_blocked_on_clarification_and_approval_pending`, `test_pipeline_blocked_with_stale_authorization`, `test_no_pipeline_side_effect_before_gate`, `test_ordinary_task_behavior_under_explicit_legacy_mode` |

### Verification commands (final round)
1. `python3 -m unittest core.tests.test_phase02_execution_boundary -v` — exit `0`, `Ran 17 tests ... OK`; log `/data/data/com.termux/files/usr/tmp/opencode/p2final-t5.log`.
2. `python3 -m unittest core.tests.test_phase02_request_intelligence core.tests.test_phase02_corrections core.tests.test_phase02_execution_boundary` — exit `0`, `Ran 96 tests in 0.625s — OK`; log `/data/data/com.termux/files/usr/tmp/opencode/p2final-target.log`.
3. `python3 -m unittest discover -s core/tests -p 'test_*.py'` — exit `0`, `Ran 343 tests in 44.557s — OK` (343 PASS / 0 FAIL / 0 ERROR); log `/data/data/com.termux/files/usr/tmp/opencode/p2final-full3.log`.
4. Fresh isolated `AI_FACTORY_TEST_ROOT` on every run; no CI or visual-browser verification was performed.

### Execution-entry-point coverage (inspected call graph)
- `orchestrator._execute()` — gate first, before pipeline early-return, agent work, tool calls and `factory.generate`.
- `pipeline.execute()` — gate before any stage; called both from `_execute()` and directly by legacy tests.
- `server.Handler._api_factory_generate()` — gate before direct model generation.
- `orchestrator._worker()` / `start()` reach execution only through `_execute()`, so they inherit the gate.
- `tool_executor.execute()` is a Phase 01 governed path; Phase 02 does not weaken its receipt/idempotency guarantees (full suite green).

### Handoff contract and honest limitation
Phase 02 does NOT implement specialist execution, product generation or bounded production adapters. The unrestricted legacy `Task` pipeline has no exact bounded plan to bind, so canonical mode FAILS CLOSED for it. A later authorized phase must provide an execution adapter that supplies the exact approved plan (steps, project, goal, scope), present a genuine explicit confirmation, and atomically consume the capability via `consume_execution()` immediately before the side effect. A read-only `can_execute()` result is never permission. This is deliberately not implemented here to avoid out-of-scope production functionality. CI/GitHub Actions and browser/viewport visual inspection remain NOT TESTED.

## True one-shot execution authorization (final correction)

### State model
`execution.state`: `ABSENT` → `PROPOSED` → `AUTHORIZED` → `CONSUMED` (terminal for that capability).

1. `propose_execution()` — creates a confirmation token and stores `PROPOSED`. This is NOT authorization; `can_execute()` still returns `no-execution-authorization`.
2. `confirm_execution()` — requires presenting that genuine one-shot token (and a valid panel session when one is supplied); validates every binding; consumes the confirmation token; transitions `PROPOSED` → `AUTHORIZED`; records `actor_trusted=True` only because an explicit confirmation action occurred (never from a bare actor string). High/critical requests additionally require an already-approved `approval` record.
3. `consume_execution()` — under the canonical request's `flock` lock, validates the exact project, goal, scope, revision, request hash, plan hash, ordered steps, actor/session and expiry, then transitions `AUTHORIZED` → `CONSUMED` and persists BEFORE returning. A second attempt, concurrent processes, stale/altered data, corrupt storage or a persistence failure all fail closed. A failed execution after consumption is not restored: a new explicit authorization is required.

`can_execute()` remains READ-ONLY and must never be used as the execution edge; `canonical_execution_gate()` calls `consume_execution()`.

### Verification
- `python3 -m unittest core.tests.test_phase02_one_shot -v` — exit `0`, `Ran 15 tests ... OK`; log `/data/data/com.termux/files/usr/tmp/opencode/p2oneshot-t2.log`.
- `python3 -m unittest core.tests.test_phase02_request_intelligence core.tests.test_phase02_corrections core.tests.test_phase02_execution_boundary core.tests.test_phase02_one_shot` — exit `0`, `Ran 111 tests in 0.802s — OK`; log `/data/data/com.termux/files/usr/tmp/opencode/p2oneshot-target.log`.
- `python3 -m unittest discover -s core/tests -p 'test_*.py'` — exit `0`, `Ran 358 tests in 45.610s — OK` (358 PASS / 0 FAIL / 0 ERROR); log `/data/data/com.termux/files/usr/tmp/opencode/p2oneshot-full.log`.
- Multi-process replay used `multiprocessing.Barrier` and two real child processes sharing the canonical store: exactly one consumption succeeded; no timing sleeps were used.

### Exact guarantee and limits
One capability authorizes exactly one bounded consumption attempt for one exact request revision/plan/project/goal/scope/actor/session. It does NOT guarantee the external side effect itself is exactly-once; providers/adapters without transactional idempotency remain outside the guarantee (Phase 01 receipts govern tool execution). Consent and identity are separate: a confirmation token proves explicit consent only; `actor_trusted=True` requires a validated authenticated panel session bound to the original proposal. A caller-supplied name alone never grants trust. Canonical mode never falls back to unrestricted legacy execution, and no CI/browser/live-provider/payment/specialist claim is made.


## Final security & consistency corrections (session / ownership / consent)

| Correction | Evidence |
|---|---|
| Session binding cannot be bypassed: the original confirmation record's `session_id` is read and strictly enforced at confirmation and consumption; missing, different, invalid or expired sessions are rejected; anonymous proposals cannot be upgraded by an arbitrary session string | `core/tests/test_phase02_session_trust.py` (`test_bound_proposal_*`, `test_consumption_bound_session_cannot_be_bypassed`, `test_anonymous_proposal_cannot_upgrade_identity`) |
| Project ownership: `rec["project"]` must equal the supplied project at proposal, confirmation and atomic consumption; cross-project authorization fails closed | `test_wrong_project_at_proposal_rejected`, `test_wrong_project_at_confirmation_rejected`, `test_wrong_project_at_consumption_rejected`, `test_correct_project_through_all_steps_consumes_once` |
| Consent vs authenticated identity: token → `consent_confirmed=True`; only a validated bound panel session → `actor_trusted=True` and `identity_mode="authenticated"`; otherwise `identity_mode="self-declared"`; high/critical approval remains separate; API views expose no token or session | `test_token_without_session_consent_only`, `test_fabricated_actor_never_trusted_without_session`, `test_high_critical_still_requires_approval`, `test_api_view_exposes_no_token_or_session` |

Verification: `python3 -m unittest core.tests.test_phase02_session_trust -v` — exit `0`, `Ran 14 tests ... OK` (`…/p2consist-t4.log`); all Phase 02 suites `Ran 125 tests ... OK` (`…/p2consist-target.log`); full suite `Ran 372 tests in 55.612s — OK`, 372 PASS / 0 FAIL / 0 ERROR (`…/p2consist-full-final.log`). `py_compile` on changed Python files: exit `0`. CI/GitHub Actions, browser/mobile visual inspection, live providers and payments: NOT TESTED.

## Accurate guarantee and limitations

- **PASS (local, deterministic):** one canonical structured request per intake, versioned and persisted; intent/requirements/domain/platform/risk/complexity/clarification/plan/approval behave as specified; high-risk work cannot be approved without an exact bound one-shot approval; Phase 01 idempotency suite remains green.
- **NOT TESTED:** model-assisted classification (not implemented — deterministic baseline only), live providers, paid actions, specialist execution, product generation, real browser/viewport visual inspection (security policy / no browser available). No such result is claimed.
- **LIMITS:** Persian/English classification is keyword-and-pattern based, not a language model; ambiguous poetic or highly indirect wording may be classified `other` and correctly routed to clarification. Risk rules are explicit and conservative, not a substitute for SecurityPolicy. `flock` is advisory and requires all writers to use this module. The orchestrator integration is a non-breaking compatibility adapter; canonical gating is enforced through the Phase 02 API and request lifecycle, while legacy task execution remains on the accepted Phase 01 path in `canonical` mode and can be switched to `legacy` mode without deleting records. ADR-011..014 remain unresolved and untouched.
