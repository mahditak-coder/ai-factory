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

## Labels and limitations
- **PASS:** implemented registry/engine functions and all executed tests (444/444 core tests green).
- **FAIL:** none in executed checks.
- **NOT TESTED:** GitHub Actions/CI (no workflow run observed), live providers, paid operations, real specialist execution, product production/build, browser/mobile visual inspection. No PASS claimed for these.
- Specialist capability discovery is contract-based and does not assert operational tools/providers exist; that verification belongs to later authorized phases.
- Phase 04 is **NOT ACCEPTED — READY FOR FINAL INDEPENDENT REVIEW**; Phase 05 is NOT authorized.
