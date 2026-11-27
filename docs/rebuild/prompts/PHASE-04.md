# PHASE 04 EXECUTION MISSION — Canonical Specialist Engine

This is a copy-paste-ready mission for a future AI Factory session. GitHub is the source of truth. Do not infer completion from chat history.

## Objective
Create one canonical specialist catalog and verified execution/handoff engine.

## Entry criteria
- Explicit user authorization for this phase.
- Previous phase acceptance verified from `CURRENT-STATE.md` and git history.
- Current branch and working tree inspected.

## Dependencies
- Requires: Phase 01, Phase 03
- Previous phase acceptance must be verified from `CURRENT-STATE.md` and git history before work starts.

## Scope
Source areas likely affected: core/specialists.py, core/workflow_roles.py, core/agent_collaboration.py, core/workflow_executor.py, core/orchestrator.py

## Required deliverables
Single role table; stable IDs; activation criteria; inputs, artifacts, tools, scopes, dependencies, acceptance, retry/failure behavior; dynamic team composition; capability discovery; verified artifact hashes; full-text handoff with hash revalidation.

## Migrations
Reconcile ROLES, EXTRA_ROLES, agents/*.json and workflow DAGs into one registry; keep aliases for compatibility.

## Security and permission requirements
Enforce scopes from role contracts; no prompt-only permission; no stale PASS after failed runs.

## Tests and evidence
Tests for role discovery, dynamic selection, handoff hash, stale-run rejection, final-response selection and permission denial.

## Acceptance criteria
All required roles discoverable; no duplicate registry drift; dependency artifacts verified by current hash; first-assistant heuristic removed or validated.

## Explicit out-of-scope
No domain production implementations, no critic loop, no release system.

## Rollback strategy
Compatibility shims for old role IDs; feature flag for new planner.

## Stop conditions
Role contract conflict that cannot be resolved without changing approved architecture.

## Required user approvals
Approval for canonical catalog changes affecting policy or permissions.

## Exit criteria
- All acceptance criteria met with recorded evidence.
- Relevant tests executed with PASS/FAIL/NOT TESTED labels.
- `CURRENT-STATE.md` and `CHANGELOG.md` updated.
- Separate phase commit created.

## Definition of DONE
Catalog and engine tests pass, decisions recorded, phase commit.

## Execution instructions (mandatory for every Phase 04)
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

