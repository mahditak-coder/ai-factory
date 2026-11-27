# PHASE 06 EXECUTION MISSION — Professional Production Engine

This is a copy-paste-ready mission for a future AI Factory session. GitHub is the source of truth. Do not infer completion from chat history.

## Objective
Replace placeholder production behavior with real, capability-verified generation and build workflows.

## Entry criteria
- Explicit user authorization for this phase.
- Previous phase acceptance verified from `CURRENT-STATE.md` and git history.
- Current branch and working tree inspected.

## Dependencies
- Requires: Phase 04, Phase 05
- Previous phase acceptance must be verified from `CURRENT-STATE.md` and git history before work starts.

## Scope
Source areas likely affected: core/workflow_executor.py, core/image_remote.py, core/image_governance.py, core/image_assets.py, core/engines/*, core/tool_registry.py, core/engine_discovery.py

## Required deliverables
Artifact versioning/provenance; real asset pipelines; honest image/audio provider records; build engines based on detected capability; removal of generic SVG/tone placeholders from production paths.

## Migrations
Versioned asset store migration; mark legacy placeholder assets as non-production.

## Security and permission requirements
Provider authentication from environment only; explicit cost approval; no paid fallback; path containment.

## Tests and evidence
Mocked provider tests plus real local toolchain tests; PNG/audio validation; capability detection tests.

## Acceptance criteria
No placeholder asset can be marked production-ready; every asset has provider/tool, model, hash, cost and limits; builds use real detected tools.

## Explicit out-of-scope
No new product strategy, no critique workflow, no release packaging.

## Rollback strategy
Keep old executors behind flags; preserve generated asset history.

## Stop conditions
No verified provider/tool for a required professional output.

## Required user approvals
Explicit approval before any paid provider or destructive build action.

## Exit criteria
- All acceptance criteria met with recorded evidence.
- Relevant tests executed with PASS/FAIL/NOT TESTED labels.
- `CURRENT-STATE.md` and `CHANGELOG.md` updated.
- Separate phase commit created.

## Definition of DONE
Production contracts accepted, tests pass, phase commit.

## Execution instructions (mandatory for every Phase 06)
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

