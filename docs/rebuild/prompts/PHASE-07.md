# PHASE 07 EXECUTION MISSION — Collaboration, Critique and Revision

This is a copy-paste-ready mission for a future AI Factory session. GitHub is the source of truth. Do not infer completion from chat history.

## Objective
Make independent critique and bounded revision a first-class governed workflow.

## Entry criteria
- Explicit user authorization for this phase.
- Previous phase acceptance verified from `CURRENT-STATE.md` and git history.
- Current branch and working tree inspected.

## Dependencies
- Requires: Phase 04, Phase 06
- Previous phase acceptance must be verified from `CURRENT-STATE.md` and git history before work starts.

## Scope
Source areas likely affected: core/agent_collaboration.py, core/workflow.py, core/tasks.py, core/decisions.py (if introduced), dashboard/workflow.js

## Required deliverables
Independent Critic role; alternative comparison rubric; multi-round revision; conflict detection; decision ledger with chosen/rejected options; user review UI; max critique cycles.

## Migrations
Import historical review decisions; preserve old approvals and artifact versions.

## Security and permission requirements
Producer must not be sole approver for high-impact work; no auto-approval; decisions recorded.

## Tests and evidence
Tests for critic independence, comparison scoring, conflict detection, revision routing, cycle caps and decision history.

## Acceptance criteria
Critic is separate from producer; every rejection has reason and owner; revision creates new version without overwriting; infinite loops prevented.

## Explicit out-of-scope
No unified quality evaluators, no release, no new product features.

## Rollback strategy
Decision ledger additive; revert critic activation flag if needed.

## Stop conditions
Contradiction between producer and critic that no recorded rule can resolve.

## Required user approvals
User decision for high-impact unresolved conflicts.

## Exit criteria
- All acceptance criteria met with recorded evidence.
- Relevant tests executed with PASS/FAIL/NOT TESTED labels.
- `CURRENT-STATE.md` and `CHANGELOG.md` updated.
- Separate phase commit created.

## Definition of DONE
Critic workflow accepted, tests pass, phase commit.

## Execution instructions (mandatory for every Phase 07)
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

## Execution record (append-only, 2026-11-28)
- Executed on `rebuild/phase-07-collaboration-critique` from exact accepted
  Phase 06 commit `b7182f83771b7af12937acc5e257a5307e34986e`.
- Implemented: canonical Critic contract, `core/critique.py`, append-only
  decision ledger, bounded 3-cycle revision with HOLD, conflict detection,
  additive API routes and safe-DOM review card in the existing workflow UI.
- Tests: Phase 07 Python 18/18 PASS; safe-DOM JS 5/5 checks PASS; full core
  675/675 PASS. Evidence: `docs/rebuild/evidence/PHASE-07-COLLABORATION-CRITIQUE.md`.
- NOT TESTED: real HTTP/auth round-trip, real browser visual inspection, live
  model execution, different-model independence, CI. Phase 08 NOT started.

## Mandatory completion record (append-only, 2026-11-28)
Real workflow-driven Critic/revision/decision path implemented and proven by 10 E2E tests plus the full core suite (685 PASS). Real HTTP socket, browser visual inspection and live model NOT TESTED. Phase 07 submitted for independent review; Phase 08 NOT started.

## Comprehensive corrective record (append-only, 2026-11-28)
Real critique/revision/decision corrections implemented; 23 new corrective tests and full suite 708 PASS. Real HTTP socket, browser visual and live model NOT TESTED. Phase 07 submitted for independent review; Phase 08 NOT started.

## Final remediation record (append-only, 2026-11-28)
Real producer-connected revision, mandatory decision fingerprint, true idempotency and real HTTP round-trip implemented; full suite 719 PASS. Phase 07 submitted for independent review; Phase 08 NOT started.

## Final closure record (append-only)
Re-audit defects fixed; 74 Phase 07 tests and 731 core tests PASS. Live model/CI/browser/cross-process stage execution NOT TESTED. Phase 07 submitted for independent review; Phase 08 NOT started.
