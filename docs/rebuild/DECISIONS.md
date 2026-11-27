# Architectural Decisions

Numbered decisions for the rebuild. Status may be ACCEPTED, PROPOSED or UNRESOLVED. Unresolved decisions must not be silently made during a phase.

- **ADR-001 — Single canonical orchestrator state machine** — ACCEPTED. Rationale: duplicated execution paths and stale state caused drift. Alternatives: keep legacy paths. Consequence: Phase 01 must migrate state with idempotency.
- **ADR-002 — Delegation over self-reference in ModelGateway seam** — ACCEPTED. Rationale: `oc_state()` recursion is P0. Consequence: production test must not mock the seam.
- **ADR-003 — One specialist catalog** — ACCEPTED. Rationale: ROLES/EXTRA_ROLES/agents/workflow DAG drift. Consequence: aliases retained, no duplicate registries.
- **ADR-004 — Dynamic activation, not all agents always** — ACCEPTED. Rationale: quality and cost. Consequence: activation matrix required in Phase 05.
- **ADR-005 — Independent Critic for high-impact acceptance** — ACCEPTED. Rationale: producer cannot be sole authority. Consequence: Phase 07.
- **ADR-006 — Evidence-bound quality decisions** — ACCEPTED. Rationale: report writing is not gate passing. Consequence: Phase 08.
- **ADR-007 — Severity-first release gate** — ACCEPTED. Rationale: score cannot hide Critical. Consequence: Phase 09.
- **ADR-008 — Product domain independent of delivery platform** — ACCEPTED. Rationale: game may target Android/Web/Desktop. Consequence: Phase 02/05.
- **ADR-009 — Environment-only credentials** — ACCEPTED. Rationale: public repository safety. Consequence: no config secrets ever.
- **ADR-010 — No fake PASS** — ACCEPTED. Rationale: audit requirement. Consequence: PASS/FAIL/NOT TESTED evidence rules.
- **ADR-011 — Target game engine and 3D scope** — UNRESOLVED. Options: Android-first 2D/2.5D toolchain, Godot/Unity later with capability proof. Must be resolved with user before Phase 06 production decisions.
- **ADR-012 — Paid image/audio providers** — UNRESOLVED. Options: remain free-model-first, or add verified paid providers with explicit cost approval. Must not be assumed.
- **ADR-013 — Browser screenshot tool policy** — UNRESOLVED. Options: add narrowly scoped approved screenshot tool, or accept visual QA NOT TESTED. SecurityPolicy must not be bypassed.
- **ADR-014 — Checksum repair ownership** — PROPOSED. Repair as separate audit-maintenance change, never silently inside product phase.
