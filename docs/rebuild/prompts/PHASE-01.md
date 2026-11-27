# PHASE 01 EXECUTION MISSION — Core Foundation and Unified Orchestration

This is a copy-paste-ready mission for a future AI Factory session. GitHub is the source of truth. Do not infer completion from chat history.

## Objective
Eliminate duplicated execution paths and make state, events, idempotency, recovery and cancellation trustworthy.

## Entry criteria
- Explicit user authorization for this phase.
- Previous phase acceptance verified from `CURRENT-STATE.md` and git history.
- Current branch and working tree inspected.

## Dependencies
- Requires: none
- Previous phase acceptance must be verified from `CURRENT-STATE.md` and git history before work starts.

## Scope
Source areas likely affected: core/orchestrator.py, core/model_gateway.py, core/tool_executor.py, core/workflow.py, core/tasks.py, core/agent_events.py, core/tool_events.py, core/paths.py, core/config.py

## Required deliverables
Fix oc_state self-recursion; canonical runtime state machine; one event envelope; replay-safe tool/workflow tokens; crash recovery for stale RUNNING stages; enforceable cancellation; ModelGateway seam with injectable verified providers.

## Migrations
Workflow store schema versioning with a reversible migration from the current JSON files; no silent data loss.

## Security and permission requirements
Tool scopes, permissions and project-path containment remain final authority; no broadened shell or network policy.

## Tests and evidence
Unit and integration tests for oc_state delegation, duplicate replay, stale RUNNING recovery, cancellation, event ordering and persistence; exact commands and results recorded.

## Acceptance criteria
No recursive seam; duplicate call_id/token never re-executes; restart resumes rather than deadlocks; all phase tests PASS with logs.

## Explicit out-of-scope
No new specialist roles, no product strategy work, no UI redesign, no release features.

## Rollback strategy
Feature flags around new state paths; retain previous store snapshot; revert without touching user data.

## Stop conditions
Unsafe migration, unresolved state ownership, or inability to prove idempotency/recovery.

## Required user approvals
User approval required for persistent-state migration and any destructive recovery behavior.

## Exit criteria
- All acceptance criteria met with recorded evidence.
- Relevant tests executed with PASS/FAIL/NOT TESTED labels.
- `CURRENT-STATE.md` and `CHANGELOG.md` updated.
- Separate phase commit created.

## Definition of DONE
Canonical documents updated with accepted decisions, tests attached, separate phase commit.

## Execution instructions (mandatory for every Phase 01)
1. Read all canonical rebuild documents first: `docs/rebuild/CURRENT-STATE.md`, `MASTER-ARCHITECTURE.md`, `REBUILD-ROADMAP.md`, `DECISIONS.md`, `ACCEPTANCE-CRITERIA.md`, `CONTINUE-HERE.md`.
2. Inspect the current Git branch, commit and working tree before changing anything; verify previous phase acceptance and required commit.
3. Work ONLY within this approved phase scope; do not start or implement any later phase.
4. Preserve all user data, uncommitted changes and unrelated files; use an isolated clone/worktree if needed.
5. Ask only essential user approval questions; do not block ordinary, low-risk, reversible work.
6. Implement real changes, not a report-only exercise; generated files alone are not acceptance.
7. Run relevant tests; record exact commands, output paths and results.
8. Label every result PASS / FAIL / NOT TESTED; never hide a failing test.
9. Never fabricate evidence, assets, specialist work or successful provider calls.
10. Update `CURRENT-STATE.md` and `CHANGELOG.md` with real state and commit references.
11. Commit the accepted phase separately with a clear message; no force push.
12. Stop if a critical dependency, ambiguity or safety issue blocks progress; report it precisely.
13. Never start the next phase automatically; wait for explicit user authorization.
14. Do not silently alter approved architecture; record new decisions in `DECISIONS.md`.

## Completion gate
This phase is DONE only when all deliverables, tests, evidence, document updates and a separate commit are verified. A written report without implementation and evidence is NOT DONE.

