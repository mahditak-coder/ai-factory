# Phase 03 — Research, Innovation & Product Strategy: implementation and evidence

> خلاصه فارسی: موتور تحقیق با منبع و provenance، مقایسهٔ گزینهها، ارزیابی امکانپذیری و Product Brief نسخهدارreal ساخته شد. هیچ ادعای تأییدنشده PASS نشد، تحقیق پولی بدون مجوز اجرا نشد و تأیید نهایی محدودهٔ محصول به کاربر واگذار شد.

Base: accepted Phase 02 commit `075921b5f0b8ebe7638b0d38c6394c6402c94541` on `rebuild/phase-02-request-intelligence`; Phase 03 branch `rebuild/phase-03-research-product-strategy` created from that exact SHA (verified locally: branch HEAD == SHA, parent == `9103a2154c0e07ec4664eda194662207a4848e9e`, clean tree).

## Documentation consistency correction
- The Phase 02 `CURRENT-STATE.md` still recorded `Phase 02 status: NOT ACCEPTED — READY FOR INDEPENDENT REVIEW` while the final independent review accepted Phase 02. This is now recorded transparently in the Phase 03 branch: `Last accepted phase: 02`, `Phase 02: ACCEPTED` with basis (commit `075921b5f0b8ebe7638b0d38c6394c6402c94541`, review decision 2026-11-28, scope = Phase 02 request intelligence and all accepted corrections through authoritative identity attribution). Prior entries are marked historical/superseded, not deleted or forged.
- Git evidence was consistent with the decision (remote Phase 02 HEAD equals the required SHA); no stop condition was triggered.

## Implemented capabilities (`core/research_product.py`)

| Deliverable | Implementation |
|---|---|
| A — Research Engine | `add_source()` with source URL/title/claim, claim grade (`verified_fact`, `hypothesis`, `estimate`, `unknown`), access time, researcher/method/hash provenance, idempotent IDs; `sources()`/`source()` retrieval; `assess_sources()` detects insufficient evidence, unverified claims and contradictory verified claims. Untrusted text is stored as data and never interpreted as instructions. |
| B — Alternative Analysis | `add_alternative()` with transparent scores for cost/complexity/risk/time/feasibility, pros/cons/limits and evidence source IDs; `reject_alternative()` with mandatory reason; `compare_alternatives()` with explicit `evidence_ok`, `score_total` and blocked `auto_select` when evidence or criteria are missing. |
| C — Feasibility | `assess_feasibility()` evaluates technical/operational/economic/practical dimensions independently (`verified`/`unknown`/`blocked`), records dependencies, risks, environment limits and `requires_human_review`. Unknown estimates stay unknown; no fabricated prices, market data, model capability or test result. |
| D — Product Brief | `create_brief()`/`revise_brief()` produce stable-ID, versioned, hashed records with status/timestamps/history; prior versions remain readable as `SUPERSEDED` and are copied into `brief_history`; `write_brief_artifact()` writes a real JSON artifact file with SHA-256. All required sections are present (problem, goal, users, needs, definition, value proposition, scope, out-of-scope, priorities, alternatives, assumptions, risks, dependencies, feasibility, acceptance criteria, success metrics, sources). |
| E — Acceptance Criteria | `validate_acceptance_criteria()` requires non-empty, unique IDs, descriptions, measurable/testable flags and source/assumption links; unknown/unverified items are listed explicitly. `propose_brief_approval()`/`approve_brief()` bind final scope/success acceptance to brief ID + hash + one-shot token; the system never marks its own brief final. |

## Security, cost and authority
- Paid research is separated from free research: `propose_paid_research()` only records an explicit approval request; `run_paid_research()` refuses without a token and, even with one, returns `BLOCKED / paid_executed=False` because no paid provider exists in Phase 03. No model/provider/specialist call is made.
- Canonical Phase 01/02 idempotency and request lifecycle remain intact (full suite green); no parallel request registry or orchestrator was introduced.

## Real artifacts
- `docs/product/brief-v1.json` — brief ID `brief-p3-demo-v1`, record hash `efc67e79d899058b3a22e4ca79a709bf15bf4eeb9068b3ef7dc122e257db7109`, file SHA-256 `33203151beaff9058f5ea6620bf4f0c11aeb50e18feb8b6d13f7c805e2cdbd`.
- `docs/product/acceptance-criteria-v1.json` — bound to the brief hash, `valid: true`, explicit unknowns `ac-1:status`, `ac-2:status`.
- Both artifacts were produced by the implemented module from a real isolated run, then committed; they are DRAFT / `PENDING_USER_APPROVAL`, not FINAL or ACCEPTED.

## Verification (all executed locally, isolated `AI_FACTORY_TEST_ROOT`)
1. `python3 -m unittest core.tests.test_phase03_research_product -v` — exit `0`, `Ran 27 tests ... OK`; log `/data/data/com.termux/files/usr/tmp/opencode/p3-target3.log`.
2. `python3 -m unittest discover -s core/tests -p 'test_*.py'` — exit `0`, `Ran 407 tests in 45.941s — OK` (407 PASS / 0 FAIL / 0 ERROR); log `/data/data/com.termux/files/usr/tmp/opencode/p3-full.log`.
3. `python3 -m py_compile core/research_product.py core/tests/test_phase03_research_product.py` — exit `0`.
4. Artifact hash verification (local command): SHA-256 of `docs/product/brief-v1.json` equals the recorded artifact SHA.

Coverage includes: source registration/retrieval/provenance, idempotence, invalid/incomplete source rejection, unverified claims never treated as PASS, contradictory sources, untrusted text as data, paid research gating, alternative comparison and rejection reasons, blocked auto-selection, feasibility dimensions, acceptance validation and unknowns, brief versioning/hash/history, real artifact file, user-approval binding, read-only approved brief, corrupt store fail-closed, and Phase 02 request compatibility.


## Independent-audit repair (same branch)

| Finding | Final control |
|---|---|
| Caller could declare `verified_fact` and be trusted | `claimed_type` records the declaration; `verification_status` is system-controlled and always `unverified` in Phase 03. No caller field (`verification_status`, `provenance`, verifier, timestamps) is accepted; forged fields raise `ResearchError`. Provenance records declaration method/time/hash only and leaves verification method/time/verifier as `None`. `.invalid`, unreachable and unchecked URLs are explicitly marked `reserved-or-unreachable-url` / `no-trusted-verifier-in-phase03`. |
| Unverified evidence could make `sufficient=True` / auto-select | `assess_sources()` counts only system-verified sources (none exist); `sufficient` is always false with the reason `no-trusted-verifier`. Declared-only contradictions are reported separately as `declared_conflicts` and never used as verified evidence. `compare_alternatives()` blocks auto-selection without verified evidence. |
| Brief approval race | The entire approval path re-reads the current brief INSIDE `_store_lock()` and atomically validates brief ID, project, current hash and exact `PENDING_USER_APPROVAL` status, token payload bindings and session binding, consumes the confirmation token, writes approval and saves — all in one critical section. A revision or status change invalidates the old token/hash. Identity follows Phase 02: a trusted panel session yields `panel_admin`; otherwise the caller label is recorded as `declared_actor` with `actor_trusted=False`. |
| Artifact path traversal | `_safe_project_dir()` enforces a strict allowlisted project ID, rejects absolute paths, `.`/`..`, separators, symlink components and any resolved path outside `config.PROJECTS_DIR`, BEFORE `mkdir`/write. |
| Ambiguous score direction | Scores are stored as `utility_scores` with `cost_utility`/`complexity_utility`/`risk_utility`/`time_utility`/`feasibility_utility`, scale 0..5, `score_direction="higher_is_better"`; 5 means cheapest/simplest/lowest-risk/fastest/most-feasible. Raw amounts are never summed as if higher were worse. |

Verification after repair (local, isolated store):
- `python3 -m unittest core.tests.test_phase03_security_repair -v` — exit `0`, `Ran 18 tests ... OK`; log `/data/data/com.termux/files/usr/tmp/opencode/p3rep-t4.log`.
- `python3 -m unittest core.tests.test_phase03_research_product core.tests.test_phase03_security_repair` — exit `0`, `Ran 45 tests ... OK`; log `/data/data/com.termux/files/usr/tmp/opencode/p3rep-target.log`.
- `python3 -m unittest discover -s core/tests -p 'test_*.py'` — exit `0`, `Ran 425 tests in 45.658s — OK`; log `/data/data/com.termux/files/usr/tmp/opencode/p3rep-full.log`.
- `python3 -m py_compile` on changed Python files — exit `0`.

Atomic-guarantee scope: the brief approval guarantee applies only inside `_store_lock()` for the canonical product store (plus the confirmation token store operations performed within that same scope). It does not make an external side effect exactly-once, and no specialist/product execution is performed in Phase 03.

Sample artifacts note: `docs/product/brief-v1.json` and `docs/product/acceptance-criteria-v1.json` were generated by the original Phase 03 implementation and remain DRAFT examples with their recorded hashes; they are NOT real market research, NOT provider-verified, and NOT user-approved. They were not rewritten during this repair.

CI/GitHub Actions: NOT TESTED (no workflow run observed). Live providers, payments, market research, specialist execution and browser/mobile visual inspection: NOT TESTED.


## Final minimal regression fix (option name shadowing)

- Finding: in `add_alternative()`, the criterion loop reused the variable `name`, which also held the option's real name, so the stored/returned name could become the last criterion key (e.g. `cost_utility`).
- Fix: the criterion loop variable was renamed to `score_key`; the option `name` is untouched on every path. No data structure, API, verification, approval, token, lock, path-containment or security-policy change was made.
- Regression: `test_option_name_survives_utility_scoring_and_comparison` registers two clearly named options with five distinct utility values, checks both names directly in storage, checks both in `compare_alternatives()` output, and asserts neither name equals any criterion key.
- Verification: new test `Ran 1 test ... OK` (exit 0); targeted Phase 03 `Ran 46 tests in 0.143s — OK` (exit 0); full suite `Ran 426 tests in 45.723s — OK` (426 PASS / 0 FAIL / 0 ERROR, exit 0); `py_compile` exit `0`. Logs: `/data/data/com.termux/files/usr/tmp/opencode/p3final-target.log`, `/data/data/com.termux/files/usr/tmp/opencode/p3final-full.log`. CI/GitHub Actions: NOT TESTED (no workflow run observed).

## Labels and limitations
- **PASS:** all implemented and executed gates above; 426/426 core tests green after the final minimal fix (425/425 after the earlier repair).
- **FAIL:** none in executed checks.
- **NOT TESTED:** GitHub Actions/CI (no workflow run), live providers, payments, specialist execution, product implementation/build, browser/mobile visual inspection. No such success is claimed.
- **AWAITING USER:** final product scope and success criteria approval; no unverified external claim is presented as fact; ADR-011..014 remain unresolved and untouched.
- Exactly-once external side effects are not claimed; Phase 01 at-most-once execution claims and request idempotency remain the governing guarantees for tool execution.
