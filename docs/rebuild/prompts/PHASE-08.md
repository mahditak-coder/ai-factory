# PHASE 08 EXECUTION MISSION — Unified Quality, Testing and Security

This is a copy-paste-ready mission for a future AI Factory session. GitHub is the source of truth. Do not infer completion from chat history.

## Objective
Replace parallel quality systems with one fail-closed, evidence-based decision framework.

## Entry criteria
- Explicit user authorization for this phase.
- Previous phase acceptance verified from `CURRENT-STATE.md` and git history.
- Current branch and working tree inspected.

## Dependencies
- Requires: Phase 06, Phase 07
- Previous phase acceptance must be verified from `CURRENT-STATE.md` and git history before work starts.

## Scope
Source areas likely affected: core/quality.py, core/quality_gate.py, core/quality_checks.py, core/action_gate.py, core/runner.py, core/typetests.py, core/security_gate.py

## Required deliverables
One quality decision vocabulary; product-specific evaluators; functional, UX, accessibility, visual, performance, security and privacy evidence; execution-success vs acceptance separation; report-to-gate consistency.

## Migrations
Map NEEDS_REVIEW/HOLD/BLOCK RELEASE into one status set; retain historical reports read-only.

## Security and permission requirements
Security decision cannot be inferred from file creation; tool PASS cannot become product PASS without acceptance evidence.

## Tests and evidence
Tests for severity-first decision, injected defects, gate consistency, evidence attachment and stale-report rejection.

## Acceptance criteria
A written report never passes its own gate; Critical always blocks; HOLD requires explicit recorded decision; all evidence file paths real.

## Explicit out-of-scope
No packaging, no deployment, no Workspace redesign.

## Rollback strategy
Quality decision translation layer; keep old evaluators read-only.

## Stop conditions
Missing evaluator for a mandatory product dimension.

## Required user approvals
Explicit decision for HOLD overrides; no auto-override.

## Exit criteria
- All acceptance criteria met with recorded evidence.
- Relevant tests executed with PASS/FAIL/NOT TESTED labels.
- `CURRENT-STATE.md` and `CHANGELOG.md` updated.
- Separate phase commit created.

## Definition of DONE
Quality framework accepted, tests pass, phase commit.

## Execution instructions (mandatory for every Phase 08)
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

