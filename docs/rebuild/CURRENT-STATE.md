# Current State

> خلاصه فارسی: این Blueprint فقط سند است؛ بازسازی شروع نشده، فاز فعلی None و آخرین فاز پذیرفتهشده None است.

- Audit snapshot: `2bf75e834d0444ac5cfef767261757324cbefee8` on `audit/full-source-review`
- Documentation branch: `rebuild/blueprint-v1` created from the exact audit commit
- Blueprint commit: f7553323055fec120faf8d3a708230ac09ff83a6
- Rebuild status: **NOT STARTED**
- Last accepted phase: **NONE**
- Active phase: **NONE**
- Next allowed action: wait for explicit user authorization to start Phase 01 only.

## Audit status and limitations
- Audit source state: local `~/AI-Factory` HEAD `3878db271313415656328ec57595d1fe87c5fa3a`, branch `main`, 151 uncommitted items, zero remotes, version `0.47.0`.
- Audit snapshot has 256 tracked files and 368 SHA256SUMS entries; 112 checksum entries do not match tracked files (mostly bytecode) and one tracked file lacks a checksum. This is an open audit finding, not repaired here.
- Test report: 221 PASS, 1 FAIL, 0 ERROR; 222 tests executed; failing test `test_root_isolated_when_env_set`. The suite is NOT fully green.
- Live provider, model, service and payment paths: NOT TESTED.
- Real browser screenshot inspection: BLOCKED by security policy; no fabricated screenshot.
- Not all specialists are independently validated; professional image/audio output is not proven.

## Open blockers
1. P0 `core/orchestrator.py:31-34` `oc_state()` self-recursion in the audited snapshot.
2. Checksum mismatch and stale test-status contradiction must remain recorded until a separate audit-maintenance change.
3. Canonical specialist registry and workflow registry are inconsistent.
4. No authenticated live provider capability verification.
5. Public onboarding lacks license/CI/setup verification.

## Next action
Only after explicit user approval: execute `prompts/PHASE-01.md` on a separate branch/worktree, preserving the original local source and all uncommitted work.
