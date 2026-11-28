# Phase 07 — Collaboration, Independent Critique & Bounded Revision (evidence)

Branch `rebuild/phase-07-collaboration-critique`, created from the exact accepted
Phase 06 commit `b7182f83771b7af12937acc5e257a5307e34986e`. GitHub entry state,
ancestry (Phase 04/05/06) and clean working tree verified before editing. The
accepted Phase 05 base `8209449e6d32a5e6368faae8365e07114b915e45` is an ancestor.
`~/AI-Factory` was never touched.

## Documentation reconciliation (append-only)
- `CURRENT-STATE.md`: Phase 06 recorded **ACCEPTED** by independent review at
  `b7182f8…`, Phase 07 **ACTIVE/authorized**, Phase 08 **NOT AUTHORIZED**, with
  earlier records preserved and superseded — not deleted or rewritten.
- `CHANGELOG.md`, `CONTINUE-HERE.md`: append-only superseding pointers added.
- `DECISIONS.md`: ADR-026 recorded PROPOSED / AWAITING INDEPENDENT ACCEPTANCE.

## Pre-implementation audit decisions
- ONE canonical registry is preserved: `independent-critic` was added to the
  existing `EXTRA_ROLES` table (40 → 41 registry IDs; `DOMAIN_ROLES` stays 16).
  No second catalog and no `agents/critic.json` was created.
- Reused unchanged: `specialist_engine` leases/fences/role authorization,
  `confirmations` one-shot tokens, `panelauth.principal_for_session`,
  `paths.project_path` containment, `production_assets.sha256_file`,
  `image_assets`-style immutable versioning, safe DOM `el()` and the existing
  CSRF-wrapped `post()` in the workflow UI.
- `independent_qa()` was NOT relabelled as a Critic decision: it remains a QA
  execution stage. A Critic verdict requires its own role, lease, version,
  hash-bound findings and decision ledger row.

## What was built
- `core/critique.py`: Critic flag (fail-closed), `start_review` (exact artifact
  version/SHA-256 + own canonical lease), immutable versioned artifacts with
  sidecars, versioned deterministic rubric (`validate_rubric`/`evaluate`) with
  Critical findings blocking independently of aggregate score, stable finding
  IDs and history, conflict detection with explicit reasons/owners, bounded
  revision (`MAX_CYCLES = 3`, idempotent HOLD on the fourth attempt), and one
  append-only cross-process decision ledger.
- Decision ledger schema: `af-critique-1.0`; entries carry stable `id`, `kind`,
  `seq`, timestamp, project/workflow/stage, artifact path/SHA-256, review ID,
  selected option, rejected options WITH reasons, evidence refs, authenticated
  `decision_maker` (never a caller string), cycle and previous-decision links.
  Storage uses `flock`, unique temp files and atomic replace; corrupt/unreadable
  stores fail closed without overwrite; duplicate IDs and decisions are rejected.
- Migration: `migrate_legacy_approvals()` is additive and non-destructive;
  historical workflow approvals are recorded with
  `is_critic_approval=False` and their original provenance.
- Additive API routes inside the existing panel gate:
  `GET /api/workflow/critique` and `POST /api/workflow/critique/flag`.
- Existing-workflow UI integration: a compact RTL/Persian review card rendered
  only with `textContent` through the existing `el()` helper; no `innerHTML`
  for untrusted text.

## Evidence classification
- **MOCKED:** confirmation transport, session identity (test helper) and fake DOM
  in `test_phase07_workflow_js.mjs`. No live provider/model call and no spend.
- **REAL:** real canonical registry, real leases, real `flock`-guarded ledger,
  real hashes and files under an isolated `AI_FACTORY_TEST_ROOT`.
- **PASS:** Phase 07 Python suite 18/18; safe-DOM JS suite 5/5 checks; full core
  suite 675/675 (68.300s); `py_compile` and `git diff --check` clean.
- **FAIL:** none in final runs. Regression during development: adding the Critic
  to `DOMAIN_ROLES` broke the accepted 16-domain contract; it was moved to
  `EXTRA_ROLES` and the full suite rerun green.
- **BLOCKED / NOT TESTED:** real HTTP authentication/CSRF round-trip for the
  two new routes; real browser visual inspection; live specialist/model
  execution of a Critic turn; different-model independence (NOT claimed);
  CI; Phase 08 unified evaluator (out of scope); release/publication.

Logs: `…/p7-t7.log` (Phase 07 suite), `…/p7-full2.log` (full core), plus the
Node JS check output recorded in the delivery report.

**Proposed decision: NOT ACCEPTED by the implementing agent** — submitted for
independent GitHub review. Phase 08 was NOT started.


## MANDATORY COMPLETION — real end-to-end path (append-only)

Continues from commit `422e894cef41970f295c69794d019bb19dbfcb55` on the same branch.

### Root gaps closed
1. Critic was only callable as a library; it is now driven by the REAL workflow
   path: `critique.run_series(project, stage_id)` reads the current workflow
   stage version and artifact SHA-256, starts an independent canonical lease,
   reviews the exact file on disk, records evidence-bound findings, evaluates
   the rubric, detects conflicts, writes an immutable verdict and loops.
2. `request_revision()` no longer only appends text: `revise_artifact()` writes
   a NEW immutable versioned file with real lineage, records a new workflow
   version via `workflow.record_external_version()`, and invalidates prior
   decisions bound to the changed artifact.
3. Verdict semantics separated from human approval: Critic may only recommend
   `pass` / `revise` / `reject`. A forged `approve` verdict fails closed.
   Only `human_decision()` with a real one-shot confirmation and trusted
   session principal records an approval and calls `workflow.approve()`.
4. Finding validation requires a stable recomputed ID, correct artifact hash,
   history and evidence with a REAL file path whose SHA-256 matches.
5. Three-cycle cap is enforced in `run_series`; the fourth cycle creates HOLD
   and no further artifact version. API/UI cannot raise it.
6. API handlers `POST /api/workflow/critique/run` and
   `POST /api/workflow/critique/decide` drive the same real path, inside the
   existing panel gate; the second derives identity from the session cookie.

### Real E2E evidence (MOCKED transport only, no live provider/paid call)
- Real workflow created; Producer stage wrote `spec/proposal.md` via the real
  `workflow.run_stage` executor path with a real SHA-256.
- Non-empty artifact: 1 review round → PASS recommendation → human decision →
  workflow stage APPROVED.
- Empty artifact: Critical finding → real revised file (new SHA) → v1 file
  unchanged → second review PASS → prior decision invalidated.
- Three forced revision rounds → HOLD, exactly three workflow versions; no
  fourth version.
- API handler path exercised with the real server handler methods; missing
  session returns 401.
- 10 E2E tests + 18 prior Phase 07 tests = 28 PASS; full core suite 685 PASS
  (70.994s); JS safe-DOM check 5/5; `py_compile` and `git diff --check` clean.

### NOT TESTED / BLOCKED
- Real HTTP socket round-trip with browser login (handler-level harness used).
- Real browser visual inspection (fake-DOM behavioural test used).
- Live Critic model turn / different-model independence (NOT claimed).
- CI. No external blocker; all required in-repo criteria implemented.

Logs: `…/p7e-t4.log`, `…/p7e-full.log`, JS check output in delivery report.

## COMPREHENSIVE CORRECTIVE MISSION — real critique, revision and decision

Continues from `edb732d27bd70319875b9603a4ab4bcdb6b8eb2b` on the same branch.

### Root causes fixed
1. Every acceptance criterion now has its own real predicate. Scores are never
   defaulted to 5; unknown criteria stay UNKNOWN with score 0. Empty files,
   HTML-comment-only files and placeholder-only text are rejected.
2. `revise_artifact()` no longer appends HTML comments. It applies concrete
   corrections, re-verifies the SAME criteria on the new bytes and refuses
   FAIL/HOLD when a correction is not applicable or changes nothing. The old
   version remains immutable; the new version is a real file with a real SHA.
3. Findings are validated inside `record_verdict()` as well as in direct calls:
   stable ID bound to review/version/SHA/criterion, review ID, history, evidence
   path and matching SHA, severity, owner and correction are all required.
4. Human decisions require a latest valid verdict bound to the CURRENT SHA, an
   allowed option, confirmation fingerprint, a trusted session and a real
   `workflow.approve()` result. HOLD/REJECT cannot be approved. Failure between
   ledger write and workflow approval is recoverable via `recover_decision()`.
5. The whole series is guarded by a cross-process flock bound to a stable
   workflow/stage key; cycle counting and HOLD use that same key. Repeated
   requests are idempotent and a fourth version cannot be created.

### Tests (MOCKED transport / browser only; no live provider or paid call)
- 23 new corrective tests: content truth, placeholder rejection, per-criterion
  evaluation, SHA stop, forged findings, real revision, old-verdict rejection,
  forged/replayed confirmation, invalid session/option, approval failure and
  recovery, deterministic thread/process races, fourth-cycle block, idempotency,
  restart lineage, session/CSRF gates and API/decision parity.
- Phase 07 suites 28/28; full core suite **708/708 PASS** (94.367s); JS safe-DOM
  5/5; `py_compile` and `git diff --check` clean.
- NOT TESTED: real HTTP socket round-trip, real browser visual inspection, live
  Critic model turn, different-model independence (NOT claimed), CI.

Logs: `…/p7c-new6.log`, `…/p7c-full.log`.

## FINAL REMEDIATION — real producer, mandatory fingerprint, true idempotency

Continues from `cab0e7998dca30b93c4930e5ddb8c1cd35dec678`.

Fixed root causes:
1. Revision now calls the real Producer execution path (`workflow_executor.run`,
   overridable only at the external-model boundary). Generic templates,
   placeholders, boilerplate and unchanged text are rejected by
   `is_task_relevant()`. Producer failure or unavailable capability returns a
   RECOVERABLE/HOLD result with the exact reason, never a fabricated PASS.
   The original artifact bytes are restored before a new immutable versioned
   file is written; all criteria are re-evaluated on the new bytes.
2. `decision_fp` is MANDATORY. `decision_fingerprint()` now binds project,
   workflow, stage, review, artifact path+SHA, option, rejected options and
   reasons/owners, evidence, cycle, authenticated principal and session.
   `decide()` independently recomputes it and rejects omission or alteration.
   `workflow.approve()` refuses any non-ledger approval once a governed
   decision exists, closing the direct API bypass.
3. Review IDs are deterministic; cached verdicts/revisions are returned on
   repeats; the whole series uses a cross-process flock. Repeated requests
   produce no duplicate reviews, verdicts, leases, versions or workflow
   transitions. Recovery marks pending decisions so replay is a no-op.

Tests: final suite 11/11 (includes a REAL HTTP socket round-trip with panel
login, CSRF and unauthenticated 401), Phase 07 62/62, full core **719/719 PASS**
(71.798s). `py_compile` and `git diff --check` clean. CI, real browser and live
model remain NOT TESTED.

## FINAL CLOSURE RE-AUDIT — approval, verdict, multilingual, crash recovery

Continues from `513960c85676456d41bb2cf71c0a53c32c7d01e7`.

Fixed: workflow-level `critic_required` policy and version/SHA-bound governed
approval in `workflow.approve()` (no ledger decision ⇒ fail closed); `decide()`
is now a complete gate (review/verdict/PASS/workflow/option/identity/one-shot);
failed required criteria and unresolved conflicts block PASS; rubric scores are
recomputed from real artifact bytes; conflict detection no longer compares an
option id with a finding owner; Persian/mixed relevance with coverage ratio and
keyword-stuffing rejection; atomic artifact writes with fsync and no short
writes; durable revision-pending/recovery for every crash point; version/
criteria/rubric-aware review identity; project-wide series lock; current-stage
enforcement; decision IDs include review/path/actor.

Tests: new final2 suite 12/12; Phase 07 total 74/74; full core **731/731 PASS**
(94.901s); JS safe-DOM 5/5; `py_compile` and `git diff --check` clean.

Honest remaining limitations: no live Critic model turn (structural-only review;
semantic quality NOT VERIFIED); different-model independence NOT claimed; CI,
real browser and cross-process simultaneous *stage execution* NOT TESTED.
