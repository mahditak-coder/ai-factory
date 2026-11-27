# AI Factory — Strategic Capability Assessment

Scope: read-only source review, one read-only test discovery, no source/project changes, no workflow execution/approval. HEAD `3878db2`, uncommitted 150, Health 200. Workflow `wf-2cb419bb6cf3` remains `AWAITING_USER_REVIEW` at `s06-ui`, rev 92; `s07-tech25d` PENDING.

Status legend: **IV** implemented & verified · **INV** implemented but not verified · **P** partial · **M** missing · **B** blocked · **BR** broken production defect.

## 1. Capability Matrix

| Capability | Current Status | Evidence | Missing Requirements | Priority |
|---|---|---|---|---|
| Local-only server, auth, CSRF, SSE | IV | `core/server.py:394-742`, `:308-335`; `core/panelauth.py:1-18,33-49`; SSE `:860-987` | Remote/LAN access is intentionally absent | P2 |
| ModelGateway routing | IV (single adapter) | `core/model_gateway.py:43-171`; `core/opencode_bridge.py:135-221,395-495` | Second verified text provider; health/cost-aware routing | P1 |
| Tool Registry / Executor / SecurityPolicy | IV | `core/tool_registry.py:13-15,221-271`; `core/tool_executor.py:46-198`; `core/boundary.py:73-337` | None for core safety | P0 |
| Durable state / task / event stores | IV | `core/workflow.py:19,42-62`; `core/tasks.py:88-124`; `core/agent_events.py:24-29,126-166` | Cross-store schema migrations | P2 |
| Human-gated workflow | IV | `core/workflow.py:181-213,314-339,436-488`; `dashboard/workflow.js:81-236` | UI for critique ledger | P1 |
| Specialist contracts | P | `core/specialists.py:74-166` (9), `:170-249` (15 extras); `core/agent_collaboration.py:207-311` | 4 requested roles missing; `_BY_ID` `:253` excludes EXTRA_ROLES from `parallel_groups`/`specialist_plan` | P0 |
| Product Director / Game Designer / Art Director / QA / Security contracts | IV | `data/specialist_runs.json`, `data/workflow_state.json` versions 3/5/6/3 etc. | Game Developer/QA/Security stages still PENDING | P1 |
| Researcher / Narrative Designer / Learning Designer / Independent Critic | M | no definitions in `core/specialists.py`, `core/workflow_roles.py`, `agents/` | Add roles, artifacts, tools, scopes, DAG placement | P0 |
| Multi-alternative generation & comparison | P | `core/engine_selection.py:20-53`; `core/agent_loop.py:28,127-140`; `core/boundary.py:262-297` | Generic artifact alternative matrix & scoring | P1 |
| Critique loop | P | human review `docs/test-reports/ui-ux-designer/USER-REVIEW.md:10-47`; independent QA `agent_collaboration.py:284-311` | Autonomous Independent Critic, bounded cycles, decision ledger | P0 |
| Cross-document consistency / contradiction detection | P | `agent_collaboration.py:135-149` only detects post-acceptance hash change; `workflow.py:134-178` hash integrity | Semantic consistency graph across spec docs | P0 |
| Revision routing & re-evaluation | IV | `workflow.py:357-372,401-421`; `workflow_executor.py:148-151`; `orchestrator.py:770,843` | Critic-driven re-review loop | P1 |
| Infinite-loop prevention | IV | `orchestrator.py:663-675`; `agent_loop.py:74-156`; `agent_planner.py:193-224` | Critic cycle cap extension | P1 |
| High-risk user approval | IV | `agent_policy.py:22-54`; `confirmations.py:53-95`; `image_governance.py:69-180` | None | P0 |
| Quality gate / real tests / false PASS block | IV | `core/quality_gate.py:47-89,99-271`; `runner.py:415-500`; `typetests.py:730-838`; `factory.py:777-812` | Test suite currently red (see below) | P0 |
| Version history / restart persistence | IV | `workflow.py:59-62,272-283`; `project-limitations.md`; `data/specialist_runs.json` | None | P1 |
| Game design docs coverage | P | existing: Product Brief, GDD, Art Direction, Character Bible, Environment Design, UX/UI Spec in `projects/afaccept-1795683260/spec/` | Narrative Bible, Educational Matrix, Audio Bible, Game/Technical Architecture standalone, Level Design standalone, Test Strategy, Release Plan | P0 |
| Android build / packaging | IV | `core/engines/android.py:286-739`; `core/server.py:1922-2045`; 24 APK/AAB under `exports/` | Game engine integration for Robāh Danā | P1 |
| Workspace CRUD / preview / logs | IV | `core/server.py:2883-3010,2636-2690`; `dashboard/workspace.html:115-200`; `runner.py:225-326` | None core | P2 |
| Mobile UI testing | P | `quality_checks.py:119-161` static tokens; live preview HTTP verified `docs/test-reports/ui-ux-designer/PREVIEW.md` | Emulation/click-flow; visual render blocked by policy | P1 |
| Browser screenshot / visual QA | B | `boundary.py:119-172`; `quality_checks.py:60-99`; real probe returned `command-not-allowed` | Narrow approved screenshot tool/scope | P1 |
| Real game engines / 3D | B | `engine_discovery.py:57-68`; `engine_catalog.py:69-88`; `engine_selection.py:23-43` | Install approved engine or accept 2D/Android-only scope | P1 |
| Paid image providers | INV | adapters `core/image_remote.py`, mocked tests only; keys unset | Auth verification, configured costs, explicit approval | P2 |
| Orchestrator state seam | BR | `core/orchestrator.py:31-33` `return oc_state(session_id)` infinite recursion; callers `:186,199,259,529` | Delegate to `gateway().state` + regression test | P0 |

Current test discovery (read-only): `python3 -m unittest discover -s core/tests -p 'test_*.py'` → **Ran 217 tests, 3 failures, 2 errors**. Failures/errors: `test_af29_perception` (2), `test_c1_auth` (1), `test_af35_integrity` (1), `test_core_units` (1). Most are isolation/fixture-environment issues, but the suite is not green and must be treated as NOT VERIFIED.

## 2. Multi-agent reality

Requested roles:
- **Actually executable with model, instruction, scopes, artifacts and recorded runs:** Product Director, Game Designer, Art Director, UX/UI Designer (current stage), Developer/QA/Security/Release as contracts with no game-project runs yet.
- **Contract exists but not yet executed for this product:** Technical Artist 2.5D, Animator, Audio Designer, Environment Artist (executed+approved in v3), Developer, QA, Security, Release.
- **Missing entirely:** Researcher, Narrative Designer, Learning Designer, Game Architect / Technical Architect (exact roles), Independent Critic.
- **Planning gap:** `_BY_ID` in `specialists.py:253` contains only original 9 roles, so `parallel_groups` and `orchestrator.specialist_plan` do not see the 15 EXTRA_ROLES; the human-gated workflow uses `workflow_roles.py` instead. This is a real dual-system inconsistency.

## 3. Critique and decision loop

Factory can generate alternatives (engine ranking, fix strategies, safe fallbacks), route revisions, preserve versions and history, and block on user approval. It cannot yet run an autonomous independent critic, compare competing design options with a reusable rubric, detect cross-document contradictions, or maintain a machine-readable decision ledger of rejected options. The best critique so far is a human-produced report (`USER-REVIEW.md`) with 13 defects, severity, page scores and NEEDS REVISION — real evidence, but not an implemented agent capability.

## 4. Game design completeness

Produced with real artifacts and hashes: Product Brief v3, GDD v5, Art Direction v6, Character Bible v3 + SVG sheet, Environment Design v3 + level-map SVG, UX/UI Spec v2 + wireframe v2. Missing as standalone artifacts: Narrative Bible, Educational Content Matrix, Audio Bible, Game Architecture, Technical Architecture, Level Design, Test Strategy, Release Plan. No automated consistency checker links these documents; contradictions (e.g., M8 Dana-only guide vs three-character universe, meta type `webapp` vs Android target) were found manually and recorded in decision docs.

## 5. Quality management

Measurable acceptance sections exist in `ArtifactSpec.validate`; real tests boot products and probe HTTP; statuses follow `opstatus.py` with SKIP never PASS; false PASS is blocked by no-coverage gates and severity-first release decisions; failures route to retry/replan with caps; versions and history persist across restart. Current weakness: the repository-wide suite is red (217 tests, 3 failures + 2 errors), so "all tests green" is not currently true.

## 6. Real product-building power

Workspace can create/rename/delete projects, edit files, run code, preview static services, stream logs, run tests, export ZIP, build signed APK/AAB with the real Android toolchain, and serve a local preview on the device (`http://127.0.0.1:8610/` verified by HTTP 200). It cannot yet render/screenshot HTML under SecurityPolicy, emulate mobile clicks, build with Unity/Godot/Flutter, or run a real 3D game pipeline. The Robāh Danā project is a design stack plus an iframe preview; no game source, executable, APK or release evidence exists for it.

## 7. Top 10 weaknesses (priority order)

1. `oc_state()` infinite recursion in production orchestrator — any real call can recurse until stack overflow.
2. Incomplete specialist roster: Researcher, Narrative Designer, Learning Designer, Independent Critic, Game/Technical Architect missing.
3. `specialist_plan`/`parallel_groups` ignore `EXTRA_ROLES`, creating a dual planning system.
4. Game pipeline stops at UI; Developer, QA, Security, Release stages unexecuted for the product.
5. Missing six-plus required design artifacts (Narrative, Learning, Audio, architectures, Level Design, Test Strategy, Release Plan).
6. No cross-document consistency checker; contradictions found manually.
7. No autonomous critic or bounded critique/decision ledger.
8. Test suite currently red; historical green reports are stale relative to current discovery.
9. Visual/mobile QA blocked by SecurityPolicy; no real browser render or click-flow evidence.
10. Real game engines and 3D are BLOCKED; production-quality art identity cannot be guaranteed by the free reference-less image model.

## 8. Proposed next architecture

- **Core seam repair:** `oc_state` delegates to `gateway().state`; add a production-path test that does not mock the symbol.
- **Unified specialist registry:** one role table; `parallel_groups` and `specialist_plan` use all roles; add the 5 missing roles with real artifacts/scopes.
- **Critic service:** Independent Critic with structured review schema, rubric-based comparison of ≥2 alternatives, severity/status, max 2 critique cycles, and a decision ledger recording chosen/rejected options and reasons.
- **Consistency graph:** declarative dependencies and contradiction rules across the 15 design docs; blocking gate on unresolved contradictions.
- **Doc generators:** missing artifacts produced as governed files with required sections and hashes.
- **Mobile QA:** a narrowly scoped, SecurityPolicy-approved screenshot/preview tool; device/emulation click-flow harness.
- **Provider health:** text routing with ≥2 authenticated providers; image providers verified live, with cost and approval gates.
- **Production build:** real engine path or honest permanent BLOCKED scope; Android build and release evidence tied to the workflow.

## 9. Essential vs non-essential

**Essential:** items 1-8 above, plus green test suite and an executed Developer→QA→Security→Release chain on at least one product.
**Non-essential now:** paid image generation, 3D engine adoption, full mobile device farm, visual polish beyond usability, additional agent roles beyond the requested 13.

## 10. Development order

1. Fix recursion and restore green tests.
2. Unify specialist registry and add missing roles.
3. Add critic service + decision ledger.
4. Add consistency graph + missing docs.
5. Add approved mobile preview/screenshot path.
6. Execute developer/QA/security/release on a product.
7. Diversify providers and consider engines only after 1-6 are stable.

## 11. Proof-of-capability test plan

1. Input a Persian brief; run the full DAG with real model turns and user gates.
2. Verify 15 design artifacts exist, hashed, with consistency PASS and recorded decisions.
3. Inject two contradictions and one failed tool call; verify detection, revision routing, re-review and no fake PASS.
4. Verify restart persistence at two approval points and no duplicate stage execution.
5. Execute real build/test/preview; collect artifacts, QA/security reports, quality score and release decision.
6. Verify test suite green and no protected-project changes.

## 12. Final statement

AI Factory is a genuinely working governed studio skeleton with real tools, persistence, quality gates and human approval. It is not yet an autonomous professional product studio: the specialist roster is incomplete, the critique loop is human-only, several required design artifacts are missing, the current test suite is red, and no product has passed developer→release. The first development priority is fixing the orchestrator seam plus unifying the specialist registry, because everything else depends on reliable execution of real specialist stages.
