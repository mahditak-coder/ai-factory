# Phase 04 — Canonical Specialist Engine: implementation and evidence

> خلاصه فارسی: یک registrycanonical برای نقشها ساخته شد با ID پایدار، alias برای نامهای قدیمی، قرارداد قابل اجرا، ترکیب پویای تیم، تحویل متن کامل با هش واقعی، رد اجرای کهنه و جلوگیری از PASS کهنه. هیچ متخصصی اجرا نشد و هیچ ابزار/provider فراخوانی نشد.

Base: accepted Phase 03 commit `0889056b389632e6af721932db3a6dad31684c6a`; Phase 04 branch `rebuild/phase-04-specialist-engine` created from that exact commit. GATE 0 reconciliation commit: `56c5c1d6da8284ab6c62a90a40ecad0e6e822d17` (Phase 03 recorded ACCEPTED, Phase 04 ACTIVE/AUTHORIZED, history preserved).

## Implemented capabilities (`core/specialist_engine.py`)

| Deliverable | Implementation |
|---|---|
| 1 — One canonical registry | `canonical_registry()` merges `ROLES` and `EXTRA_ROLES` into one validated table (24 unique IDs); conflicting duplicate contracts fail closed via `EngineError`. No new competing registry. |
| 2 — Stable IDs + aliases | `LEGACY_ALIASES` maps old agent IDs (`builder`, `qa`, `designer`, `planner`, `frontend`, `releaser`, DAG variants) to canonical IDs; `resolve_id()`/`get()` apply them without creating duplicate roles. |
| 3 — Role contracts | Each contract contains inputs, artifacts (path/format/required sections), allowed scopes, dependencies, acceptance rules, activation criteria, retry/failure policy and evidence requirement. |
| 4 — Dynamic composition | `discover()` filters by product type/goal; `compose_team()` uses the accepted workflow DAG, resolves aliases, skips roles with unsatisfiable dependencies and returns a bounded ordered team. Nothing is executed. |
| 5 — Verified handoff | `verify_handoff()` reads FULL artifact text (no truncation), computes current SHA-256, revalidates against expected hashes, and reports missing/hash-mismatch failures. |
| 6 — Stale-run rejection | `start_run()` records a lease and reopens stale leases (dead PID) as `REVISION` without deleting records; `reject_stale()` and `record_result()` only accept the current run token, so a stale PASS is blocked. |
| 7 — Enforced permissions | `require_scope()` checks the contract's `allowed_scopes` only; prompt/instruction text cannot grant scopes. |
| 8 — Legacy reconciliation | `registry_consistency()` verifies every workflow DAG role resolves to the canonical registry; explicit `engine_mode()`/`set_engine_mode()` with legacy as an opt-in shim (no automatic fallback on canonical failure). |
| Final-response selection | `select_final_response()` skips aborted/error turns and picks the newest COMPLETE assistant message; returns an honest NOT OK verdict when none qualifies — no first-assistant heuristic. |

## What was NOT done (scope boundaries)
No specialist execution, no model/provider call, no product generation, no critic loop, no release system, no UI work, no Phase 05 implementation. ADR-011..014 remain unresolved and untouched.

## Verification (all local, isolated `AI_FACTORY_TEST_ROOT`)
1. `python3 -m unittest core.tests.test_phase04_specialist_engine -v` — exit `0`, `Ran 18 tests ... OK`; log `/data/data/com.termux/files/usr/tmp/opencode/p4-target2.log`.
2. `python3 -m unittest discover -s core/tests -p 'test_*.py'` — exit `0`, `Ran 444 tests in 45.916s — OK` (444 PASS / 0 FAIL / 0 ERROR); log `/data/data/com.termux/files/usr/tmp/opencode/p4-full.log`.
3. `python3 -m py_compile core/specialist_engine.py core/tests/test_phase04_specialist_engine.py` — exit `0`.
4. `registry_consistency()` on the accepted tables returned `ok=True`, 24 roles, zero problems.

Coverage: unique registry IDs, contract fields, alias resolution, dynamic selection, goal-based discovery, workflow consistency, scope grant/denial, full-text handoff with hash revalidation and tamper detection, missing artifact fail-closed, stale-run rejection, no stale PASS, current-run PASS, dead-worker stale resolution, final-response selection, failed-turn rejection, explicit feature flag, corrupt store fail-closed.

## Independent-audit repairs (same branch)

| Finding | Repair | Evidence |
|---|---|---|
| P0 store collision — engine and `agent_collaboration` shared `data/specialist_runs.json` with incompatible schemas | Canonical engine now uses `data/canonical_specialist_runs.json`; the legacy list store remains for `agent_collaboration`. `migrate_legacy_runs()` is explicit, idempotent, non-destructive; no silent migration on read | `test_01_no_silent_migration_and_legacy_preserved`, `test_02_migration_idempotent_and_restart`, `test_03_concurrent_runs_on_distinct_roles_persist` |
| P0 role scopes not enforced at tool boundary | `ToolRequest.role_id`/`enforce_role` trigger canonical contract checks inside `ToolExecutor.execute()` before adapter side effects; caller scopes and prompt text are ignored | `test_04_forged_scopes_and_prompt_cannot_grant_permission`, `test_05_missing_and_unknown_role_context_denied`, `test_06_valid_role_scope_and_session` |
| P0 concurrent run leases | One current lease per project/role under flock; live leases rejected or explicitly superseded; terminal results only from the authoritative current token; PID reuse guarded via `/proc` start time; real thread/two-process races | `test_07_two_threads_cannot_both_get_lease`, `test_08_two_processes_cannot_both_record_pass`, `test_09_supersede_and_pid_reuse_safety` |
| P0 handoff not verified/integrated | Expected producer hashes mandatory; missing expected hash fails closed; full text (no 4000-char truncation) via `agent_collaboration.verified_handoff()`; revalidated immediately before consumers | `test_10_expected_hash_required`, `test_11_changed_after_verification_and_long_text` |
| Catalog reconciliation | All `ROLES`, `EXTRA_ROLES`, `agents/*.json` and workflow DAG IDs reconciled to canonical roles or explicit excluded status | `test_12_all_legacy_sources_reconciled` |
| Final-response ordering | Selection uses trustworthy completion timestamps, never input position; aborted/incomplete turns skipped | `test_13_newest_completed_wins_regardless_of_position` |

Verification after repair (local, isolated `AI_FACTORY_TEST_ROOT`):
- `python3 -m unittest core.tests.test_phase04_audit_repair -v` — exit `0`, `Ran 13 tests ... OK`; log `/data/data/com.termux/files/usr/tmp/opencode/p4rep-t4.log`.
- `python3 -m unittest core.tests.test_phase04_specialist_engine core.tests.test_phase04_audit_repair` — exit `0`, `Ran 31 tests ... OK`; 5/5 reruns stable.
- `python3 -m unittest discover -s core/tests -p 'test_*.py'` — exit `0`, `Ran 457 tests in 45.389s — OK`; log `/data/data/com.termux/files/usr/tmp/opencode/p4rep-full.log`.
- `python3 -m py_compile` on changed Python files — exit `0`.
- CI/GitHub Actions: NOT TESTED. No real specialist execution or product generation was performed.
- ADR-024 is PROPOSED / AWAITING INDEPENDENT ACCEPTANCE, not ACCEPTED.

## Final integration repairs (same branch)

| Finding | Integration fix | Evidence |
|---|---|---|
| Role authorization not mandatory on specialist paths | `write_artifact()` and `independent_qa()` now pass canonical `role_id` with `enforce_role=True`; missing/unknown role context and forged scopes are denied before adapter side effects. Explicit `execution_context="non-specialist-explicit"` is the only non-specialist path | `test_01_write_artifact_denies_unauthorized_role`, `test_02_write_artifact_with_valid_role_and_session`, `test_03_non_specialist_context_still_works`, `test_04_ambiguous_context_denied` |
| Verified handoff not used by `run_specialist()` | Expected hashes come from authoritative accepted producer run records; producer status/artifact path/version/SHA verified; full text passed to the consumer turn and revalidated immediately before it; legacy handoff only under explicit legacy mode | `test_05_run_specialist_uses_verified_handoff`, `test_06_run_specialist_rejects_tampered_producer`, `test_07_long_producer_text_not_truncated`, `test_08_missing_producer_record_blocks`, `test_11_legacy_mode_keeps_legacy_handoff` |
| Run leases not integrated into `run_specialist()` | `start_run()` claims the lease and every terminal result goes through `record_result()` with the current token; overlapping executions produce exactly one PASS; stale/superseded workers cannot write accepted results | `test_09_overlapping_specialists_only_one_current`, `test_10_stale_lease_cannot_produce_pass` |

Verification: `python3 -m unittest core.tests.test_phase04_integration -v` — exit `0`, `Ran 11 tests ... OK` (`…/p4int-t6.log`); all Phase 04 tests `Ran 42 tests ... OK` (`…/p4int-target.log`); full suite `Ran 468 tests in 44.502s — OK` (`…/p4int-full.log`). `py_compile` exit `0`. CI/GitHub Actions: NOT TESTED. No real provider/model/paid operation or product generation occurred (the gateway was a deterministic local fixture solely for exercising the integration path). ADR-024 remains PROPOSED / AWAITING INDEPENDENT ACCEPTANCE.

## Final security and result-integrity repair (same branch)

| Finding | Repair | Evidence |
|---|---|---|
| Forged `execution_context` bypass | Retired the caller-supplied string (any non-empty value is rejected as `forged-execution-context`). Added server-minted `execution_token` via `issue_infrastructure_context()` with a TTL registry; only valid tokens authorize infrastructure tools. Specialist calls still require canonical `role_id` with `enforce_role=True`; prompt text/caller scopes cannot grant anything | `test_01_forged_execution_context_string_denied`, `test_02_server_issued_context_allowed`, `test_03_unknown_context_token_denied`, `test_04_specialist_path_without_role_denied`, `test_05_prompt_and_scopes_cannot_grant` |
| PASS despite write/persistence failure | `run_specialist()` now checks every `write_artifact()` result and validates content + SHA of newly written artifacts only; stale leases are rejected before any model/write; terminal persistence is checked and any rejection makes the public result non-PASS; failures never reuse older files as proof | `test_06_stale_worker_terminal_rejected`, `test_07_blocked_write_with_older_valid_file_no_pass`, `test_08_persistence_failure_never_pass`, `test_09_successful_current_worker`, `test_10_overlapping_only_current_pass` |
| First-assistant heuristic | Both the main turn and missing-artifact subturn use canonical `select_final_response()`; aborted/errored/incomplete/timestamp-less turns are skipped, newest completed turn wins by timestamp, list order is never a fallback | `test_11_shuffled_history_uses_newest_completed`, `test_12_no_completed_turn_is_not_pass` |

Verification: `python3 -m unittest core.tests.test_phase04_security_integrity -v` — exit `0`, `Ran 12 tests ... OK` (`…/p4sec-t4.log`); all Phase 04 tests `Ran 54 tests ... OK` (`…/p4sec-target3.log`); full suite `Ran 480 tests in 45.839s — OK` (`…/p4sec-full.log`); `py_compile` exit `0`. CI/GitHub Actions NOT TESTED. No real provider/model/paid operation or product generation occurred. ADR-024 remains PROPOSED / AWAITING INDEPENDENT ACCEPTANCE.

## Final identity enforcement repair (same branch)

| Finding | Repair | Evidence |
|---|---|---|
| ToolExecutor accepted requests with no identity | MANDATORY identity: exactly one of canonical `role_id`+`enforce_role=True` (contract-authorized) OR a bounded server-issued `capability_token`. Neither, only one half of role identity, or both identities → BLOCKED before adapter side effects | `test_01_no_identity_blocked`, `test_07_valid_role_within_contract`, `test_08_role_scope_denied`, `test_10_ambiguous_identity_blocked` |
| Forgeable/uncapped infrastructure context | `core/tool_identity.py` mints capabilities only through a trusted in-process issuer key or a validated panel session; capabilities bind exact project/tool/scope and expiry. Forged, expired, wrong-scope and wrong-project tokens fail closed; caller cannot mint a capability by choosing a principal | `test_02_forged_capability_blocked`, `test_03_expired_capability_blocked`, `test_04_wrong_scope_capability_blocked`, `test_05_wrong_project_capability_blocked`, `test_06_caller_cannot_mint_capability`, `test_09_valid_capability_bounded` |
| QA outside canonical lifecycle | `independent_qa()` now claims a canonical lease, rejects stale/superseded runs before and during its tool calls, checks every tool and artifact write, and records terminal state via `record_result()`; non-PASS persistence makes the public result non-PASS | `test_11_qa_stale_run_blocked`, `test_12_qa_failed_tool_not_pass`, `test_13_qa_failed_artifact_write_not_pass`, `test_14_qa_failed_terminal_not_pass`, `test_15_qa_current_run_pass` |

Trusted issuance boundary: `tool_identity.server_issuer_key()` is generated in server process memory at import and is used only by trusted infrastructure modules (`quality_gate`, `workflow_executor`) and the test helper that issues REAL bounded capabilities. Model prompts, caller JSON, scope strings and principal labels are never accepted as issuance authority. SecurityPolicy, confirmations, project containment and Phase 01 durable receipts remain in force; no generic bypass was added.

Verification: `python3 -m unittest core.tests.test_phase04_identity_enforcement -v` — exit `0`, `Ran 15 tests ... OK` (`…/p4ident-t3.log`); all Phase 04 tests `Ran 69 tests ... OK` (`…/p4ident-target.log`); full suite `Ran 495 tests in 42.563s — OK` (`…/p4ident-full2.log`); `py_compile` exit `0`. CI/GitHub Actions NOT TESTED. No live provider/model/paid operation or product generation occurred. ADR-024 remains PROPOSED / AWAITING INDEPENDENT ACCEPTANCE.

## Final consolidated security & lease repair (same branch)

| Finding | Repair | Evidence |
|---|---|---|
| Public issuer key exposure | `server_issuer_key()` and `_ISSUER_KEY` removed. `tool_identity` now mints capabilities only from an allowlisted server module with a registered service identity or a validated panel session; no raw key is returned or accepted. `quality_gate`/`workflow_executor` migrated to `issue_service_capability()` | `test_01_no_public_issuer_getter`, `test_02_imported_caller_cannot_mint`, `test_05_valid_service_scoped` |
| Unauthorized/ambiguous tool requests | Mandatory single identity at ToolExecutor; no identity, forged/expired/wrong-project/wrong-tool/wrong-scope capability and dual identity are BLOCKED before adapter execution (verified with adapter call counters) | `test_03_no_identity_rejected_before_adapter`, `test_04_forged_capability_rejected_before_adapter`, `test_06_role_authorization_still_enforced` |
| Specialist side effects not fenced by lease | `ToolRequest.run_token` added; `lease_fence()` holds the canonical lock across adapter execution; `write_artifact()` and `independent_qa()` propagate the same token; supersession during a tool or write cannot produce PASS | `test_07_qa_superseded_during_tests_run`, `test_08_qa_superseded_during_security_scan`, `test_09_qa_superseded_before_write` |
| QA result integrity | QA revalidates its lease after each tool, before and after the report write, verifies the actual report file and SHA-256, and records terminal state via `record_result()`; failed write/hash/persistence yields FAIL or BLOCKED, never PASS | `test_10_qa_failed_write_not_pass`, `test_11_qa_hash_mismatch_not_pass`, `test_12_qa_terminal_failure_not_pass`, `test_13_concurrent_qa_one_pass` |

Cross-path audit (whole repository, not just changed files): the only tool entry points are `agent_collaboration.write_artifact`, `agent_collaboration.independent_qa` tool calls, `quality_gate._call` and `workflow_executor._call`; each supplies exactly one valid identity model. No `server_issuer_key`/`issue_infrastructure_capability` reference remains outside negative assertions. No test-only authority bypass was introduced: the test helper provisions a real server-side panel session and calls the production issuance API.

Verification: `python3 -m unittest core.tests.test_phase04_final_security -v` — exit `0`, `Ran 14 tests ... OK` (`…/p4fin-t7.log`); all Phase 04 tests `Ran 83 tests ... OK` (`…/p4fin-target.log`); full suite `Ran 509 tests in 45.439s — OK` (`…/p4fin-full-final.log`); `py_compile` exit `0`. CI/GitHub Actions NOT TESTED. No live provider, payment or production product execution occurred. ADR-024 remains PROPOSED / AWAITING INDEPENDENT ACCEPTANCE; Phase 05 NOT AUTHORIZED.

## Absolute final authorization & lease closure (same branch)

Service/tool/scope authorization matrix (exact `(tool_id, scope)` pairs only; module names never grant arbitrary operations):
- `quality-gate`: `project.read_file:project:read`, `project.inspect:project:read`, `project.write_file:project:write`, `tests.run:tests:run`, `security.scan:security:read`, `shell.safe_run:shell:execute`.
- `workflow-executor`: `project.write_file:project:write`, `project.read_file:project:read`, `tests.run:tests:run`, `security.scan:security:read`, `shell.safe_run:shell:execute`, `media.generate_wav:media:write`.
- `infrastructure` (validated panel session only): the above plus `image.generate:image:generate`, `image.write:image:write`. A session-authenticated caller may only use a tool actually registered with the exact matching scope.

| Finding | Repair | Evidence |
|---|---|---|
| Service helpers delegated arbitrary tool authority | Exact service operation allowlists added in `tool_identity`; `quality_gate._call` and `workflow_executor._call` reject any tool/scope outside their declared operations before capability issuance and before adapters run | `test_02_quality_helper_rejects_unauthorized_tool`, `test_03_workflow_helper_rejects_unauthorized_tool`, `test_04_wrong_scope_rejected`, `test_05_legitimate_fixed_operation_works`, `test_06_imported_caller_cannot_expand_service`, `test_07_exact_service_matrix_declared` |
| `run_token` optional for role requests → unfenced specialist execution | Mandatory: role identity without `run_token` is BLOCKED before adapter; `lease_fence()` is always used for role requests (no `nullcontext` fallback). All specialist callsites (`write_artifact`, `run_specialist`, `independent_qa`) propagate the current token | `test_01_missing_token_never_reaches_adapter`, `test_02_wrong_project_token_rejected`, `test_03_superseded_token_rejected`, `test_04_current_lease_executes` |
| Supersession during a real adapter was unproven | Real ToolExecutor + real registered adapters + real `lease_fence`: a concurrent supersede attempt is held until the fenced adapter finishes; stale workers cannot write artifacts or record PASS; adapter exception/timeout/receipt failure stay non-PASS; concurrent QA yields at most one PASS | `test_05_supersede_blocked_until_adapter_finishes`, `test_06_no_artifact_write_after_supersession`, `test_07_adapter_exception_not_pass`, `test_08_receipt_failure_blocks_before_adapter`, `test_09_concurrent_qa_at_most_one_pass` |

Whole-repository audit confirmed every tool entry point has exactly one identity model: specialist role+current lease, or service capability minted from an exact allowlist / validated panel session. Role requests are fenced through the full real adapter side effect. Test harness restores any built-in tool specs it temporarily registers, so it cannot leak authority into other tests. No raw issuer secret is exposed and no test-only production bypass exists.

Verification: `python3 -m unittest core.tests.test_phase04_real_boundary -v` — exit `0`, `Ran 9 tests ... OK` (`…/p4abs-t8.log`); service boundary `Ran 7 tests ... OK` (`…/p4abs-svc.log`); all Phase 04 tests `Ran 99 tests ... OK` (`…/p4abs-target2.log`); full suite `Ran 525 tests in 55.824s — OK` (`…/p4abs-final.log`); `py_compile` exit `0`. CI/GitHub Actions NOT TESTED. No live provider/payment/production execution. ADR-024 PROPOSED / AWAITING INDEPENDENT ACCEPTANCE; Phase 05 NOT AUTHORIZED.

## Final timeout / side-effect fence closure (same branch)

Defect fixed: `execute()` previously called `cancel.set()`, recorded a BLOCKED timeout receipt, returned and released `lease_fence()` while the adapter thread could still be alive and performing side effects.

New behavior: a timeout only REQUESTS cancellation. The executor then joins the adapter thread without a further timeout while still inside the fence; only after the adapter actually exits are `_running` cleaned up and the BLOCKED/`tool-timeout` receipt finalized, with truthful evidence (`cancel_requested=True`, `cancel_acknowledged=False`, `waited_for_adapter_exit=True`). A non-cooperative adapter therefore cannot outlive its lease or overlap a superseding run.

Verification with the REAL ToolExecutor, real registered adapters and the real canonical lease store (no mocked `execute`/`lease_fence`):
- An adapter detects cancellation but deliberately ignores it, performs a delayed side effect and exits; a concurrent `start_run(..., supersede=True)` is held until that adapter actually exits.
- No PASS and no terminal receipt while the adapter is alive; timeout result finalized only after exit; lease can then be superseded.
- Missing token, receipt-store failure, adapter exception, normal completion and infrastructure identity remain fail-closed/correct.

Commands: `python3 -m unittest core.tests.test_phase04_timeout_fence -v` — exit `0`, `Ran 6 tests ... OK` (`…/p4timeout-t1.log`); combined Phase 04 `Ran 105 tests ... OK` (`…/p4timeout-target.log`); full suite `Ran 531 tests in 52.945s — OK` (`…/p4timeout-full.log`); repeated timeout+real-boundary concurrency runs 5/5 OK. `py_compile` exit `0`. CI/GitHub Actions NOT TESTED. No live provider, payment or production execution. ADR-024 PROPOSED / AWAITING INDEPENDENT ACCEPTANCE; Phase 05 NOT AUTHORIZED.

## Labels and limitations
- **PASS:** implemented registry/engine functions and all executed tests (444/444 core tests green).
- **FAIL:** none in executed checks.
- **NOT TESTED:** GitHub Actions/CI (no workflow run observed), live providers, paid operations, real specialist execution, product production/build, browser/mobile visual inspection. No PASS claimed for these.
- Specialist capability discovery is contract-based and does not assert operational tools/providers exist; that verification belongs to later authorized phases.
- Phase 04 is **NOT ACCEPTED — READY FOR FINAL INDEPENDENT REVIEW**; Phase 05 is NOT authorized.
