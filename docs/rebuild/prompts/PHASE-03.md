# PHASE 03 EXECUTION MISSION — Research, Innovation and Product Strategy

This is a copy-paste-ready mission for a future AI Factory session. GitHub is the source of truth. Do not infer completion from chat history.

## Objective
Produce an evidence-backed product definition and measurable acceptance criteria before production.

## Entry criteria
- Explicit user authorization for this phase.
- Previous phase acceptance verified from `CURRENT-STATE.md` and git history.
- Current branch and working tree inspected.

## Dependencies
- Requires: Phase 02
- Previous phase acceptance must be verified from `CURRENT-STATE.md` and git history before work starts.

## Scope
Source areas likely affected: core/specialists.py, core/workflow_roles.py, core/agent_collaboration.py, core/research* (if introduced), docs/product/*

## Required deliverables
Researcher and innovation capabilities with source tracking; alternative analysis; feasibility; Product Brief; user/market definition; scope, priorities and success metrics.

## Migrations
Map existing Product Brief artifacts into the new product record without losing history.

## Security and permission requirements
Source provenance; no unverified external claims; paid research requires approval.

## Tests and evidence
Tests for source tracking, alternative comparison, artifact acceptance and traceability; no fake evidence.

## Acceptance criteria
Product Brief and acceptance criteria are real artifacts with hashes; rejected alternatives and rationale recorded; no unverified claim marked PASS.

## Explicit out-of-scope
No implementation team execution, no UI code, no release work.

## Rollback strategy
Versioned product records; retain old briefs as read-only history.

## Stop conditions
No reliable evidence source for a critical product claim.

## Required user approvals
User approval of final product scope and success criteria.

## Exit criteria
- All acceptance criteria met with recorded evidence.
- Relevant tests executed with PASS/FAIL/NOT TESTED labels.
- `CURRENT-STATE.md` and `CHANGELOG.md` updated.
- Separate phase commit created.

## Definition of DONE
Canonical product artifacts accepted, tests pass, phase commit.

## Execution instructions (mandatory for every Phase 03)
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

