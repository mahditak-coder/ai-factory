# PHASE 05 EXECUTION MISSION — Domain Specialist Teams

This is a copy-paste-ready mission for a future AI Factory session. GitHub is the source of truth. Do not infer completion from chat history.

## Objective
Activate only the domain roles a product actually needs, with real contracts and outputs.

## Entry criteria
- Explicit user authorization for this phase.
- Previous phase acceptance verified from `CURRENT-STATE.md` and git history.
- Current branch and working tree inspected.

## Dependencies
- Requires: Phase 04
- Previous phase acceptance must be verified from `CURRENT-STATE.md` and git history before work starts.

## Scope
Source areas likely affected: core/specialists.py, core/workflow_roles.py, core/templates/*, core/prompts/* (if introduced)

## Required deliverables
Game, education, software, web, Android, design, art, audio, narrative, level, balance, accessibility and related domain roles as capabilities/subroles where appropriate; activation criteria and artifact sets.

## Migrations
Map current DAG stages to canonical roles; keep historical stage IDs as aliases.

## Security and permission requirements
No permanent all-agent execution; role scopes least privilege; external providers only when verified.

## Tests and evidence
Tests for activation matrix, role artifacts, dependency ordering and no unnecessary agent execution.

## Acceptance criteria
A simple web task does not activate game/audio/3D roles; a game task activates relevant roles only; every activated role produces required artifacts.

## Explicit out-of-scope
No production engine changes, no critique loop, no release.

## Rollback strategy
Per-domain feature flags.

## Stop conditions
Unclear domain boundaries that cause role duplication.

## Required user approvals
User approval if new high-impact roles gain tool or spending authority.

## Exit criteria
- All acceptance criteria met with recorded evidence.
- Relevant tests executed with PASS/FAIL/NOT TESTED labels.
- `CURRENT-STATE.md` and `CHANGELOG.md` updated.
- Separate phase commit created.

## Definition of DONE
Activation matrix documented, tests pass, phase commit.

## Execution instructions (mandatory for every Phase 05)
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

