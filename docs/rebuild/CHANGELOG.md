# Changelog

- 2026-11-27 — `docs: add audited AI Factory rebuild blueprint and ten phase missions` — Blueprint created from audit commit `2bf75e834d0444ac5cfef767261757324cbefee8` on branch `rebuild/blueprint-v1`. Rebuild status NOT STARTED. No product source changed.
- 2026-11-27 — `docs: correct blueprint commit reference in current state` — corrected CURRENT-STATE commit reference to `95b901d9a4bccb5d96f50c256668c2d2a904423c`.
- 2026-11-27 — Phase 01 — Core Foundation and Unified Orchestration accepted: `oc_state()` delegation, canonical event envelope, durable replay-safe ToolEvent receipts, stale RUNNING workflow recovery, observable cancellation, strict ModelGateway validation, 227/227 isolated tests PASS.
- 2026-11-27 — docs: correct Phase 01 final commit reference after amend (no source change).
- 2026-11-27 — fix: Phase 01 acceptance — atomic concurrent call-id claim and durable idempotency receipts separated from bounded event history; full suite 231/231 PASS.
- 2026-11-27 — fix: Phase 01 final hardening — durable RESERVED/IN_PROGRESS receipt before adapter, terminal finalize after execution, fail-closed corrupt/missing receipt store with explicit actor reconciliation (no destructive migration, no exactly-once side-effect claim); full suite 239/239 PASS.
- 2026-11-27 — fix: Phase 01 multi-process idempotency — flock-guarded atomic claim and state transitions, unique per-writer temp files, audited reconciliation with named actor; real multiprocessing tests; full suite 246/246 PASS. Phase 01 still awaiting independent acceptance.
