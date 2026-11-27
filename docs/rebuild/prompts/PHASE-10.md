# PHASE 10 EXECUTION MISSION — Workspace and End-to-End Acceptance

This is a copy-paste-ready mission for a future AI Factory session. GitHub is the source of truth. Do not infer completion from chat history.

## Objective
Deliver a polished mobile-first Workspace and prove the rebuilt studio end to end.

## Entry criteria
- Explicit user authorization for this phase.
- Previous phase acceptance verified from `CURRENT-STATE.md` and git history.
- Current branch and working tree inspected.

## Dependencies
- Requires: Phase 09
- Previous phase acceptance must be verified from `CURRENT-STATE.md` and git history before work starts.

## Scope
Source areas likely affected: dashboard/*, core/server.py, docs/*, scripts/ai-factory-test-e2e, .github/* (if introduced)

## Required deliverables
Coherent project lifecycle; artifact review; simple approval states; clear blockers; onboarding; CI; supported-platform verification; final E2E proof from request to release.

## Migrations
Workspace UI migration to canonical workflow state; docs and README public onboarding.

## Security and permission requirements
No secret exposure; public docs contain only sanitized examples; approval states remain explicit.

## Tests and evidence
Workspace API tests, UI static checks, E2E with real specialists, restart persistence, regression suite and documented limitations.

## Acceptance criteria
One full product path completes with evidence; rebuild suite green except explicitly documented NOT TESTED; no protected data published.

## Explicit out-of-scope
No Phase 11 and no implementation beyond this phase.

## Rollback strategy
UI feature flags; previous workspace retained until E2E acceptance.

## Stop conditions
Any unresolved audit defect or missing evidence for end-to-end acceptance.

## Required user approvals
Final user acceptance of the rebuilt studio.

## Exit criteria
- All acceptance criteria met with recorded evidence.
- Relevant tests executed with PASS/FAIL/NOT TESTED labels.
- `CURRENT-STATE.md` and `CHANGELOG.md` updated.
- Separate phase commit created.

## Definition of DONE
All canonical docs updated; E2E evidence attached; separate phase commit; rebuild status COMPLETED.

## Execution instructions (mandatory for every Phase 10)
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

