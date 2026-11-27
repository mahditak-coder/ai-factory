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
