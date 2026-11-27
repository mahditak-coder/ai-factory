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
- **ADR-015 — Canonical event envelope** — ACCEPTED (Phase 01). ToolEvent and AgentEvent use one schema-versioned envelope with event_uid and seq; legacy keys preserved for SSE/Workspace compatibility.
- **ADR-016 — Durable replay receipts** — ACCEPTED (Phase 01). A repeated `call_id` is rejected and never re-executes; the in-memory ledger no longer clears itself. Rationale: idempotency must survive restarts.
- **ADR-017 — Lease-based stale RUNNING recovery** — ACCEPTED (Phase 01). A RUNNING stage with a dead run_pid is reopened as REVISION with history; no artifact, version or approval is deleted. Rationale: crash recovery without data loss.
- **ADR-018 — Observable cancellation** — ACCEPTED (Phase 01). Cancellation requests are durably recorded and queryable; cooperative adapters may acknowledge, but a non-cooperative worker is never assumed stopped. Force-killing threads is out of scope.
- **ADR-019 — Test isolation assertion generalized** — ACCEPTED (Phase 01). Removed a hardcoded temp path prefix that made `test_root_isolated_when_env_set` fail in valid isolated roots; no product behavior changed.
