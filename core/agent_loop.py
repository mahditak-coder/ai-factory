"""AF-32 — progress-based, bounded decisions for the EXISTING agent engines.

No parallel loop engine.  This helper only tells the proven orchestrator /
pipeline whether the current work should CONTINUE, REPLAN, ASK or STOP.

Policy change from AF-31 (proven weak by AF-32 real E2E):
  * the number 3 is NO LONGER the main stopping rule;
  * continuation is driven by REAL new evidence (new tool result, different
    error, a green step, a valid plan change), not by counting tool calls;
  * repeating the same failed action with no new evidence is a dead end;
  * a hard safety cap (default 8) and a configurable wall-clock budget remain;
  * ActionGate, permissions and sensitive-action control are untouched.
"""
from __future__ import annotations

import hashlib
import logging

from core import action_gate, agent_events, agent_memory, agent_policy, opstatus

log = logging.getLogger("ai-factory.agent-loop")

SOFT_ATTEMPTS = 3          # legacy fairness threshold, not a hard stop
HARD_ATTEMPTS = 8          # secondary safety cap against infinity
MAX_WALL_S = 15 * 60
# Only actionable fix strategies; "ask-user" is a terminal ASK action, never
# a strategy injected into a repair prompt.
STRATEGIES = ("narrow-scope", "alternate-tool", "inspect-first")


def _sig(step: str, strategy: str, error: str) -> str:
    raw = "%s|%s|%s" % (step, strategy, str(error)[:180])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _seen(project: str, task_id: str, signature: str) -> bool:
    try:
        text = agent_memory.recall(project, signature, kinds=("error", "decision"),
                                   task_id=task_id, max_chars=2400)
    except Exception as exc:  # noqa: BLE001 — fail OPEN on evidence, not on guess
        log.warning("memory lookup failed (%s); treating signature as unseen", exc)
        return False
    return signature in (text or "")


def decide(project: str, task_id: str, goal: str,
           last_result: dict | None, plan_id: str = "",
           elapsed_s: float = 0.0, attempts: int = 0,
           step: str = "verify", progress: bool | None = None,
           max_wall_s: float | None = None,
           max_attempts: int | None = None) -> dict:
    """Honest, progress-aware next-action decision; never invents success."""
    try:
        verdict = action_gate.gate_test(last_result)["status"]
        if verdict == opstatus.PASS:
            return {"action": "STOP", "success": True, "attempts": attempts,
                    "reason": "شواهد واقعی موفقیت تأیید شد", "strategy": "done"}

        budget = float(max_wall_s if max_wall_s is not None else MAX_WALL_S)
        if elapsed_s > budget:
            return {"action": "STOP", "success": False, "attempts": attempts,
                    "reason": "سقف زمانی اجرا رسید — توقف صادقانه", "strategy": "timeout"}

        hard = max(int(max_attempts or HARD_ATTEMPTS), SOFT_ATTEMPTS + 1)
        if attempts >= hard:
            return {"action": "ASK", "success": False, "attempts": attempts,
                    "question": "سقف ایمنی اجرا رسید و هنوز شاهد موفقیت نیست. "
                                "آیا ادامه تحت نظارت یا توقف باreport دقیق؟",
                    "options": ["ادامه تحت نظارت", "توقف باreport"],
                    "recommended": "توقف باreport",
                    "reason": "سقف ایمنی ثانویه — نه حلقهٔ بی پایان",
                    "strategy": "safety-cap"}

        # AF-44 — a repeated REAL policy rejection is a hard limit, not a
        # strategy to cycle through.  Ask with the exact forbidden path/scope
        # instead of sending the model back to the same denied command.
        try:
            rej = agent_events.policy_rejection_stats(project, task_id=task_id)
        except Exception:  # noqa: BLE001 — fail OPEN on evidence lookup
            rej = {"total": 0, "distinct": 0, "latest": None}
        # Only a REPEATED identical denial is a dead loop. Different rejected
        # commands are exploration — let the same session recover with the
        # feedback it already received (bounded by the normal safety caps).
        if (int(rej.get("total") or 0) >= 2 and
                int(rej.get("distinct") or 0) <= 1):
            latest = rej.get("latest") or {}
            meta = latest.get("metadata") or {}
            return {"action": "ASK", "success": False, "attempts": attempts,
                    "question": (
                        "Policy بارها دستورهای خارج از محدوده را رد کرد: %s | ممنوع: %s "
                        "| محدودیت: %s. چگونه ادامه دهیم؟" % (
                            meta.get("reason") or "policy-rejection",
                            ", ".join(str(x) for x in
                                      (meta.get("forbidden") or []))[:160],
                            str(meta.get("policy_limit") or "")[:160])),
                    "options": ["تغییر مسیر امن", "توقف باreport دقیق"],
                    "recommended": "توقف باreport دقیق",
                    "reason": "policy rejection repeated %d time(s)" %
                              rej["total"],
                    "strategy": "policy-rejection"}

        decision = agent_policy.assess(goal, {"type": project})
        if agent_policy.needs_user(decision) and attempts <= 1:
            return {"action": "ASK", "success": False, "attempts": attempts,
                    "question": decision["question"],
                    "options": decision.get("options", []),
                    "recommended": decision.get("recommended", ""),
                    "reason": decision["reason"], "strategy": "ask-user"}

        err = str((last_result or {}).get("note") or
                  (last_result or {}).get("error") or verdict)

        # Progress = NEW real evidence, not merely another tool invocation.
        signatures = [_sig(step, s, err) for s in STRATEGIES]
        seen_now = any(_seen(project, task_id, s) for s in signatures)
        if progress is None:
            progress = not seen_now
        if not progress and attempts >= SOFT_ATTEMPTS:
            return {"action": "ASK", "success": False, "attempts": attempts,
                    "question": "پیشرفت واقعی متوقف شده و همان شکست تکرار میشود. "
                                "چگونه ادامه دهیم؟",
                    "options": ["تغییر مسیر و ادامه", "توقف باreport دقیق"],
                    "recommended": "توقف باreport دقیق",
                    "reason": "بنبست: تکرار خطا بدون نتیجهٔ جدید",
                    "strategy": "deadlock"}

        used = {s for s in STRATEGIES if _seen(project, task_id,
                                               _sig(step, s, err))}
        candidate = STRATEGIES[min(max(int(attempts or 0), 0),
                                   len(STRATEGIES) - 1)]
        # With REAL new evidence, reusing a strategy is legitimate; without
        # progress we must choose a different one.
        strategy = candidate if (candidate not in used or progress) else next(
            (s for s in STRATEGIES if s not in used), None)
        if strategy is None:
            return {"action": "ASK", "success": False, "attempts": attempts,
                    "question": "همهٔ مسیرهای شناختهشده امتحان شداند؛چه کنیم؟",
                    "options": ["توقف باreport", "ادامه تحت نظارت"],
                    "recommended": "توقف باreport",
                    "reason": "پایان مسیرهای معتبر", "strategy": "exhausted"}

        agent_memory.remember(project, "error", "%s: %s" % (step, err),
                              task_id=task_id, tags=[strategy, "loop",
                                                     _sig(step, strategy, err)])
        return {"action": "REPLAN", "success": False, "attempts": attempts,
                "strategy": strategy, "step": step, "progress": bool(progress),
                "reason": "شکست واقعی با مدرک — تغییر استراتژی: " + strategy}
    except Exception as exc:  # noqa: BLE001 — fail closed, never keep guessing
        log.exception("loop decision failed")
        return {"action": "ASK", "success": False, "attempts": attempts,
                "question": "حلقهٔ تصمیم قابل اجرا نبود؛ ادامه ایمن نیست. "
                            "آیا مداخلهٔ انسانی لازم است؟",
                "options": ["توقف باreport", "ادامه تحت نظارت"],
                "recommended": "توقف باreport",
                "reason": "loop helper unavailable: %s" % exc,
                "strategy": "fail-closed"}
