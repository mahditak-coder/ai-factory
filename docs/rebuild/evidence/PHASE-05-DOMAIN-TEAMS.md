# Phase 05 — Domain Specialist Teams: implementation and evidence

> خلاصه فارسی: تیمهای تخصصی بر اساس دامنه، پلتفرم، هدف، قابلیت، ریسک و وابستگیها بهطورreal و قابلتوضیح انتخاب میشوند. هیچ متخصص اضافی و هیچ اجرای متخصص/providerای در این فاز انجام نشد.

Base: `rebuild/phase-04-specialist-engine` @ `1d53571025812e3cff725c508de62a894ffd37e4` (parent `35bbf335f722696118669213903400950f8bd3b4`, Phase 03 ancestor `0889056b389632e6af721932db3a6dad31684c6a`). Ancestry verified with `git merge-base --is-ancestor` before editing. GATE 0 documentation reconciled Phase 01–04 as ACCEPTED and Phase 05 as ACTIVE/EXPLICITLY AUTHORIZED; historical statuses preserved as superseded.

## What was implemented
- `core/specialists.py`: 16 additive domain capability/subrole contracts (backend, frontend, database, API, DevOps, mobile UX, application architecture, level design, game balance, narrative, education, curriculum, learning experience, accessibility, visual design, music/sound). Each has stable ID, purpose, inputs, artifacts with required sections, dependencies, least-privilege scopes, acceptance rules, retry policy and evidence requirement.
- `core/specialist_engine.py`: single canonical registry now includes `DOMAIN_ROLES` (40 roles total); added compatibility aliases for conversational/domain names. No second registry was created.
- `core/domain_teams.py`: deterministic activation matrix. Inputs: product domain, delivery platform, user goal, explicit capabilities, constraints, dependencies, risk and project context. Output: ordered, de-duplicated, dependency-complete team with expected artifacts and scopes. It never executes a specialist or calls a provider.

## Exact activation behaviors verified
| Scenario | Result |
|---|---|
| Simple web page | 5 web roles; no game/audio/3D/character/level/balance roles |
| Game + Android | game roles + `android-developer`; no contradiction |
| Game + Web | game roles, NO Android assumption |
| Education app + Android | education roles + mobile roles, no game roles |
| Software app + Android | software + mobile roles, no game roles |
| Ambiguous multi-domain request | BLOCKED with essential clarification; no guess |
| Unknown role / unresolved dependency / cycle | BLOCKED |
| Incomplete artifact / forged scope | Rejected by validation |
| Legacy alias (`backend`, `qa`, …) | Resolves to exact canonical role |
| Feature flag OFF | Falls back to accepted Phase 04 read-only composition; no data loss and no unsafe legacy execution |
| Concurrent builds | Identical team hash, no duplicate roles |

## Security boundaries
- Least privilege: new domain roles only use `project:read` or `project:write`; no billing/payment, shell, image-generation, media-write or release-export scopes. Restrictive validation is enforced on Phase 05 roles while accepted Phase 04 contracts remain unchanged.
- ToolExecutor, lease fencing, capability issuance and SecurityPolicy were NOT changed in Phase 05.
- No permanent all-agent execution; roles activate only from evidence.
- ADR-011..014 untouched; ADR-024 unchanged (PROPOSED); ADR-025 recorded PROPOSED / AWAITING INDEPENDENT ACCEPTANCE.

## Verification
- `python3 -m unittest core.tests.test_phase05_domain_teams -v` — exit `0`, `Ran 17 tests ... OK`; log `/data/data/com.termux/files/usr/tmp/opencode/p5-target.log`.
- `python3 -m unittest discover -s core/tests -p 'test_*.py'` — exit `0`, `Ran 548 tests in 56.303s — OK`; log `/data/data/com.termux/files/usr/tmp/opencode/p5-full.log`.
- `python3 -m py_compile` for changed Python files — exit `0`.
- All runs used isolated `AI_FACTORY_TEST_ROOT`.

## Labels and limitations
- **PASS:** implemented activation matrix, 16 contracts, aliases, consistency, fail-closed cases, feature-flag compatibility and all 548 core tests.
- **FAIL:** none in executed checks.
- **NOT TESTED:** CI/GitHub Actions (no workflow run observed), live providers, paid operations, real specialist execution, product/game/app production, media generation, browser/mobile visual inspection. No such success is claimed.
- Activation is deterministic and contract-based; it does not prove that any external tool or provider capability actually exists (Phase 06+ concern).
- Phase 05 is **NOT ACCEPTED — READY FOR INDEPENDENT REVIEW**. Phase 06 is NOT AUTHORIZED.

## Independent-audit repair — dependency ordering + canonical contract validation
- Finding A: team order previously mixed workflow DAG order with alphabetical fallback, which did not guarantee dependency-before-consumer. `core/domain_teams.py` now uses Kahn topological ordering over ONLY the selected roles and their canonical (alias-resolved) dependencies; ties are broken by historical DAG stage order then canonical role ID. Unknown dependencies and cycles fail closed. DAG data and historical stage IDs are preserved (`stage_id` in team output); no extra role is activated for ordering.
- Finding B: `validate_team()` now compares role_id (canonical-resolved), expected_artifacts, inputs, dependencies, allowed_scopes and acceptance against the exact canonical contract of that role. Any scope outside the contract is rejected (not only a hardcoded blacklist); forged/missing artifacts, inputs, dependencies or acceptance fail. ToolExecutor, SecurityPolicy and Phase 04 authorization code were NOT changed by this repair.
- Tests added to `core/tests/test_phase05_domain_teams.py`: education+curriculum ordering (`education-specialist` before `curriculum-designer`), dependency-before-consumer for every team, `network:admin` / `project:delete` / `credentials:read` rejection, valid scope acceptance, forged artifact rejection, forged/removed dependency rejection, and alias resolution.
- Verification: targeted `Ran 22 tests ... OK` (`/data/data/com.termux/files/usr/tmp/opencode/p5audit-t3.log`); full suite `Ran 553 tests in 61.394s — OK` (`/data/data/com.termux/files/usr/tmp/opencode/p5audit-full.log`); `py_compile` exit `0`. CI/GitHub Actions NOT TESTED. ADR-011..014 and ADR-024 unchanged; ADR-025 remains PROPOSED. Phase 05 NOT ACCEPTED — READY FOR FINAL INDEPENDENT REVIEW.

## Final scope-contract validation repair
Finding: `validate_team()` accepted a strict subset of a role's canonical `allowed_scopes`, contradicting exact canonical contract validation.

Fix: `allowed_scopes` must now equal the canonical contract list exactly — same membership, completeness, no duplicates and the same canonical ordering. Diagnostics are explicit and deterministic: `missing-scopes`, `malformed-scopes`, `duplicate-scope`, `unauthorized-scope`, `omitted-scope`, `scope-order-mismatch`. Alias resolution, artifact/input/dependency/acceptance validation and the ToolExecutor/SecurityPolicy boundaries are unchanged.

Regression tests added to `core/tests/test_phase05_domain_teams.py`: exact list passes; removing one scope, removing all scopes, adding an unauthorized scope, duplicating a scope, reordering scopes and malformed non-list values all fail; canonical aliases still validate.

Verification: `python3 -m unittest core.tests.test_phase05_domain_teams -v` — exit `0`, `Ran 29 tests ... OK` (`/data/data/com.termux/files/usr/tmp/opencode/p5scope-t1.log`); full suite `Ran 560 tests in 52.822s — OK` (`/data/data/com.termux/files/usr/tmp/opencode/p5scope-full.log`); `py_compile` exit `0`. CI/GitHub Actions NOT TESTED. ADR-011..014, ADR-024 and ADR-025 unchanged. Phase 05 NOT ACCEPTED — READY FOR FINAL INDEPENDENT REVIEW.

## Final malformed-scope input hardening
Finding: `validate_team()` called `len(set(scopes))` on caller-supplied `allowed_scopes`; a list containing an unhashable element (nested list/dict) raised `TypeError` instead of returning a fail-closed report.

Fix: elements are checked with `isinstance(scope, str)` BEFORE any set operation or membership check. Non-string elements (nested list, dict, int, bool, None, or mixed lists) return `ok=False` with deterministic `malformed-scope:<role>:<index>` diagnostic and never raise. All existing diagnostics (`missing-scopes`, `malformed-scopes`, `duplicate-scope`, `unauthorized-scope`, `omitted-scope`, `scope-order-mismatch`) and exact canonical list equality including ordering are preserved. ToolExecutor, SecurityPolicy, Phase 04, canonical registry and ADR statuses are unchanged.

Verification: `python3 -m unittest core.tests.test_phase05_domain_teams -v` — exit `0`, `Ran 36 tests ... OK` (`/data/data/com.termux/files/usr/tmp/opencode/p5mal-t2.log`); full suite `Ran 567 tests in 55.709s — OK` (`/data/data/com.termux/files/usr/tmp/opencode/p5mal-full.log`); `py_compile` exit `0`. CI/GitHub Actions NOT TESTED. Phase 05 NOT ACCEPTED — READY FOR FINAL INDEPENDENT REVIEW.

## FINAL CONSOLIDATED REPAIR — findings A–H (one package)
Parent: `aab033bf07d1e5306f152b5460ef3afafff6666f` (reviewed Phase 05 commit, preserved). Accepted Phase 04 base: `1d53571025812e3cff725c508de62a894ffd37e4`. Final commit SHA is the resulting branch HEAD (a commit cannot contain its own SHA); recorded exactly in the delivery report and verifiable with `git ls-remote`.

### Final activation rules and domain/platform behavior
- Domain and delivery platform are independent: `game+Android` ≠ `game+Web`; Android alone never implies game; education+Android activates education + Android roles, not game roles.
- Role ownership is authoritative and reconciled against the single canonical registry: `_GAME_ONLY` → game, `_EDUCATION_ROLES` → education, `_ANDROID_ROLES` → android, `_CROSS_DOMAIN_CREATIVE` (`narrative-designer`, `music-sound-specialist`) → media; all other roles → software. These are cross-domain capabilities, NOT game-only.
- Product-type compatibility uses canonical contract `product_types` (`*` = universal; delivery-type/domain/platform compatibility otherwise). A capability keyword can never activate a domain-incompatible role (e.g. `level`/`balance` on a web request never activates `level-designer`/`game-balance-specialist`).
- Dependency closure adds only eligible, enabled roles and drops every consumer of any ineligible/disabled dependency. The algorithm is monotonic (add, then remove only) so cycles/ineligible dependencies terminate deterministically.
- `frontend` → `ui-ux-designer` (backward compatible); `front-end` → `frontend-specialist`; `frontend-specialist` remains the canonical ID. Alias cycles, unresolved targets and unexpected canonical shadowing are reported by `alias_report()`; intended historical DAG aliases are explicitly listed.

### Feature Flag semantics and failure behavior
- `core/domain_teams.py` uses `fcntl.flock` on a stable `.lock` file plus unique `pid.thread.random.tmp` files and atomic `replace` for read/modify/write (replacing the former process-local `threading.RLock` + fixed `.tmp` path).
- JSON root must be an object; every key must be `domain:<known domain>` and every value a real boolean. The string `"false"` is NOT enabled. Unknown keys and wrong types fail closed.
- Missing file = genuine first run (default enabled); corrupt JSON, wrong root/entry types, or unreadable storage raise `FlagStoreError` and `domain_enabled()` returns `False` (fail closed) without overwriting user data. `set_domain_flag()` raises instead of silently repairing corruption.
- Every domain flag is checked for owned roles, including Android roles within software, education and game requests. A disabled secondary domain cannot leak roles through capability selection or dependency expansion. Flag-off for the primary domain falls back to the accepted Phase 04 read-only composition; no data deletion, no unsafe legacy execution path.

### Canonical role inventory, metadata and validation guarantees
- All 16 `DOMAIN_ROLES` contracts are present exactly once in the single canonical registry; `catalog_reconciliation()` now includes `DOMAIN_ROLES`, duplicate contract IDs, missing domain roles and alias problems, and reports `ok=True`.
- `constraints` and `project_context` are METADATA ONLY in Phase 05: they are type-validated, preserved in the classification context, and provably do not change the composed team hash. No deterministic selection rule is claimed for them.
- `validate_team()` now requires exact built-in `str` scope elements (`type(scope) is str`, stricter than `isinstance`), checks collection types, dependency-before-consumer ordering, and forged `title`/`domain`/`stage_id`; duplicate alias IDs and all canonical field equality checks remain. Every malformed input returns a deterministic `ok=False` report and never raises.
- `classify_context()`/`build_team()` validate `product_type`, `goal`, `capabilities`, `constraints`, `risk` and `project_context`; malformed values fail closed. Invalid risk (string or non-string) sets `risk_invalid` and still activates the canonical security role (fail closed into security, never a bypass). Unknown/ambiguous classification remains fail-closed.
- Scope: Phase 05 only composes and validates teams. No specialist execution, provider/model call, media generation, shell/network/billing/payment/credential/release authority. ToolExecutor, SecurityPolicy, capability issuance and Phase 04 lease fencing are unchanged (`git diff --name-only` confirms only `core/domain_teams.py`, `core/specialist_engine.py`, `core/tests/test_phase05_domain_teams.py` plus docs).

### Tests actually executed (isolated `AI_FACTORY_TEST_ROOT`)
| Command | Result | Counts | Exit | Evidence |
|---|---|---|---|---|
| `python3 -m unittest core.tests.test_phase05_domain_teams -v` | PASS | 64 PASS / 0 FAIL / 0 ERROR | 0 | `/data/data/com.termux/files/usr/tmp/opencode/p5cons-t7.log` |
| `python3 -m unittest discover -s core/tests -p 'test_*.py'` | PASS | 595 PASS / 0 FAIL / 0 ERROR | 0 | `/data/data/com.termux/files/usr/tmp/opencode/p5cons-full.log` (67.507s) |
| Phase 04 regressions (`test_phase04_specialist_engine`, `test_phase04_audit_repair`, `test_phase04_final_security`, `test_phase04_security_integrity`) | PASS | 57 PASS / 0 FAIL / 0 ERROR | 0 | `/data/data/com.termux/files/usr/tmp/opencode/p5cons-p04.log` |
| `python3 -m py_compile` for all changed Python files | PASS | exit 0 | 0 | inline |
| Multi-process flag concurrency (`test_55_concurrent_process_flag_writes_preserve_all`, 6 real child processes with widened read-modify-write window) | PASS | all 6 flags preserved | 0 | targeted suite log above |
| `git diff --check` whitespace check | PASS | no errors | 0 | inline |

28 new deterministic tests (37–64) cover custom string subclasses (hashable/unhashable), alias backward compatibility/collisions, product-type and cross-domain eligibility, every domain flag, corrupt/unreadable/concurrent flag storage, invalid classification inputs, invalid risk, metadata-only behavior, dependency ordering, forged metadata, duplicate aliases, malformed collection types and registry completeness. No existing test was weakened or deleted.

### Remaining NOT TESTED limitations
CI/GitHub Actions, live providers/models, payments, browser/visual inspection, real specialist/provider execution and real product/media generation remain NOT TESTED. Phase 05 is NOT integrated into the Production Engine at runtime. Phase 05 remains **NOT ACCEPTED — READY FOR FINAL INDEPENDENT REVIEW**; Phase 06 was NOT started.
