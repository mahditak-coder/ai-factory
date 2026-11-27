# PHASE 02 EXECUTION MISSION — Request Intelligence and Planning

This is a copy-paste-ready mission for a future AI Factory session. GitHub is the source of truth. Do not infer completion from chat history.

## Objective
Convert a Persian request into a structured, classified and reviewable production intent before any specialist work.

## Entry criteria
- Explicit user authorization for this phase.
- Previous phase acceptance verified from `CURRENT-STATE.md` and git history.
- Current branch and working tree inspected.

## Dependencies
- Requires: Phase 01
- Previous phase acceptance must be verified from `CURRENT-STATE.md` and git history before work starts.

## Scope
Source areas likely affected: core/agent_policy.py, core/agent_planner.py, core/agent_loop.py, core/orchestrator.py, core/tasks.py, core/server.py, dashboard/workspace.html

## Required deliverables
Unified request model; intent extraction; requirement list; essential clarification with options; risk/domain/platform/complexity classification; bounded plan; approval policy.

## Migrations
Migrate legacy requestcheck/policy/routing fields into one request record.

## Security and permission requirements
Clarification gating for sensitive, expensive or irreversible work; no auto-approval.

## Tests and evidence
Unit tests for classification, clarification, plan generation, approval routing and low-risk autonomy; integration with Phase 01 state.

## Acceptance criteria
Every request has a structured record; product domain and delivery platform are independent; high-risk work cannot proceed without explicit approval.

## Explicit out-of-scope
No specialist execution, no research production, no code generation, no build.

## Rollback strategy
Keep legacy planner behind a flag; preserve existing task records.

## Stop conditions
Ambiguous high-risk intent where clarification is impossible.

## Required user approvals
Explicit user answers to essential questions; approval of plan for sensitive work.

## Exit criteria
- All acceptance criteria met with recorded evidence.
- Relevant tests executed with PASS/FAIL/NOT TESTED labels.
- `CURRENT-STATE.md` and `CHANGELOG.md` updated.
- Separate phase commit created.

## Definition of DONE
Request schema documented, tests pass, phase commit.

## Execution instructions (mandatory for every Phase 02)
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

