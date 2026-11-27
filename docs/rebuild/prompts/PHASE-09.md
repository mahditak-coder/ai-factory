# PHASE 09 EXECUTION MISSION — Release, Delivery and Resource Governance

This is a copy-paste-ready mission for a future AI Factory session. GitHub is the source of truth. Do not infer completion from chat history.

## Objective
Make build, signing, delivery, rollback and resource limits honest and auditable.

## Entry criteria
- Explicit user authorization for this phase.
- Previous phase acceptance verified from `CURRENT-STATE.md` and git history.
- Current branch and working tree inspected.

## Dependencies
- Requires: Phase 08
- Previous phase acceptance must be verified from `CURRENT-STATE.md` and git history before work starts.

## Scope
Source areas likely affected: core/smart_release.py, core/release.py, core/release_center.py, core/engines/android.py, core/deploy.py, scripts/release/*

## Required deliverables
Release manifest with artifact hash/version; signing and integrity; delivery channels; rollback; cost/quota governance; store_requirements public template; release evidence tied to quality decision.

## Migrations
Versioned release records; sanitized public template for data/store_requirements.json.

## Security and permission requirements
Sensitive actions require confirmation; no spend without approved estimate; no fake release success.

## Tests and evidence
Tests for release decision binding, signing verification, rollback, quota enforcement and manifest integrity.

## Acceptance criteria
Release PASS only when quality decision is RELEASE and artifact verified; HOLD/BLOCK cannot be published as PASS.

## Explicit out-of-scope
No Workspace end-to-end acceptance, no new product features.

## Rollback strategy
Signed release immutability; rollback channel documented.

## Stop conditions
Unverifiable signing, missing artifact hash or unresolved billing/permission issue.

## Required user approvals
Explicit approval for signing, publishing and any paid action.

## Exit criteria
- All acceptance criteria met with recorded evidence.
- Relevant tests executed with PASS/FAIL/NOT TESTED labels.
- `CURRENT-STATE.md` and `CHANGELOG.md` updated.
- Separate phase commit created.

## Definition of DONE
Release governance accepted, tests pass, phase commit.

## Execution instructions (mandatory for every Phase 09)
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

