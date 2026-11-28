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

## Labels and limitations
- **PASS:** implemented registry/engine functions and all executed tests (444/444 core tests green).
- **FAIL:** none in executed checks.
- **NOT TESTED:** GitHub Actions/CI (no workflow run observed), live providers, paid operations, real specialist execution, product production/build, browser/mobile visual inspection. No PASS claimed for these.
- Specialist capability discovery is contract-based and does not assert operational tools/providers exist; that verification belongs to later authorized phases.
- Phase 04 is **NOT ACCEPTED — READY FOR FINAL INDEPENDENT REVIEW**; Phase 05 is NOT authorized.
