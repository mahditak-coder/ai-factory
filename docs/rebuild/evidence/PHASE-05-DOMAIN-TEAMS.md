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
