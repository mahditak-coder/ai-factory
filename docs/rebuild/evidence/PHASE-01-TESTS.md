# Phase 01 — real test evidence

Source: isolated rebuild clone `rebuild/phase-01-core-foundation`, base Blueprint `61240a7f35fb49586e4d739d8f9228ee86bc025a`.
Original local `~/AI-Factory` was not used for execution; its 151 uncommitted items and zero remotes were preserved.

## Commands and exact results
1. `python3 -m unittest core.tests.test_phase01_seams`
   - Result: `Ran 5 tests ... OK`.
   - Covers: non-mocked `oc_state()` delegation, duplicate `call_id` durability, stale RUNNING recovery, observable cancellation, canonical event envelope.
2. `python3 -m unittest core.tests.test_workflow_gate core.tests.test_afnext_p2_registry core.tests.test_af29_perception core.tests.test_afnext_gateway core.tests.test_core_units core.tests.test_orchestrator_loop core.tests.test_phase01_seams`
   - Result: `Ran 56 tests ... OK`.
3. `python3 -m unittest discover -s core/tests -p 'test_*.py'`
   - Result: `Ran 227 tests in 48.398s — OK` (exit 0).
   - Status: **227 PASS / 0 FAIL / 0 ERROR**.

All commands were run with `AI_FACTORY_TEST_ROOT` pointing to an isolated temporary root, so no source/project user data was modified.

## Previously recorded failure
Audit baseline recorded `test_root_isolated_when_env_set` as FAIL. It was caused by a hardcoded `/usr/tmp/opencode/af26-*` prefix in `core/tests/test_core_units.py`. Phase 01 generalized the assertion to compare `config.PROJECT_ROOT` with `config._TEST_ROOT` or the repository root. This is state-isolation scope, not a product feature change.

## Status legend
PASS — command really ran with the recorded result. FAIL — none. NOT TESTED — live providers, model calls, paid actions and browser screenshot (out of Phase 01 scope).
