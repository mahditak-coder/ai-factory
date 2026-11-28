# CONTINUE HERE — canonical session instructions

> خلاصه فارسی: GitHub منبع حقیقت است. وضعیت را از فایلها بخوان، نه از گفتگو. بازسازی NOT STARTED و فاز ۰۱ منتظر تأیید کاربر است.

## Read order
1. `CURRENT-STATE.md` first.
2. `MASTER-ARCHITECTURE.md`, `REBUILD-ROADMAP.md`, `DECISIONS.md`, `ACCEPTANCE-CRITERIA.md`.
3. Inspect current branch, commit and working tree.
4. Read only the prompt for the current allowed phase (`prompts/PHASE-NN.md`).

## Rules
- Do not infer completion from chat history.
- Do not skip phases; no eleventh implementation phase exists.
- Do not silently change approved architecture; record decisions in `DECISIONS.md`.
- If documents conflict, STOP and resolve before implementation.
- If evidence is missing, mark NOT TESTED.
- No PASS without actual acceptance evidence.
- Preserve original local `~/AI-Factory`; use isolated clone/worktree.
- Never commit/push to the original source; publish only the documentation branch work.
- Do not start the next phase automatically; wait for explicit user authorization.

## Current pointer
Rebuild status: NOT STARTED. Last accepted phase: NONE. Active phase: NONE. Next allowed action: explicit user authorization for Phase 01.

## Current pointer (SUPERSEDES the historical pointer above, preserved not rewritten, 2026-11-28)
GitHub is the source of truth. Rebuild status: IN PROGRESS. Last accepted phase: **06 — Professional Production Engine** — ACCEPTED by independent review at commit `b7182f83771b7af12937acc5e257a5307e34986e` on `rebuild/phase-06-production-engine`. Active phase: **07 — Collaboration, Independent Critique & Bounded Revision** — ACTIVE and explicitly user-authorized on `rebuild/phase-07-collaboration-critique`. Next allowed action: implement and submit Phase 07 for independent review. Phase 08 is NOT authorized. Read `CURRENT-STATE.md` first and verify Git state before any action.

## Current pointer (append-only update, 2026-11-28)
Phase 07 mandatory completion executed on `rebuild/phase-07-collaboration-critique`; real E2E workflow path proven with 10 E2E tests and full core suite 685/685 PASS. Phase 07 awaits independent review; Phase 08 NOT authorized. Last accepted phase remains 06.
