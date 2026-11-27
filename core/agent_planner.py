"""AF-31 — controlled dynamic planning built on existing project facts.

Creates a small dependency-aware plan from the real snapshot and memory; never
starts a second engine.  Replanning happens ONLY on new real evidence (tool
failure, requirement change, blocked step) and never repeats a PASS step.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import core.config as config
from core import opstatus

_STORE = config.PROJECT_ROOT / "data" / "agent_plans.json"
_LOCK = threading.RLock()
MAX_PLANS = 120
MAX_REPLANS = 2
PASS, FAIL, BLOCKED, SKIP, QUEUED, RUNNING = (
    opstatus.PASS, opstatus.FAIL, opstatus.BLOCKED,
    opstatus.SKIP, "QUEUED", "RUNNING")


def _load() -> list[dict]:
    try:
        d = json.loads(_STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _save(plans: list[dict]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(plans[-MAX_PLANS:], ensure_ascii=False, indent=1),
                   encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(_STORE)


def _put(plan: dict) -> dict:
    with _LOCK:
        plans = [p for p in _load() if p.get("id") != plan.get("id")]
        plans.append(plan)
        _save(plans)
    return plan


def get(plan_id: str) -> dict | None:
    return next((p for p in _load() if p.get("id") == plan_id), None)


def create(project: str, goal: str, snapshot: dict | None = None,
           decision: dict | None = None, task_id: str = "") -> dict:
    """Build a small real plan; dependent steps are explicit."""
    snap = snapshot or {}
    sections = snap.get("sections") or {}
    identity = sections.get("identity") or {}
    kind = str(identity.get("type") or snap.get("project_type") or "webpage")
    commands = sections.get("commands") or {}
    environment = sections.get("environment") or {}
    caps = environment.get("capabilities") or {}
    # AF-42 — choose ONLY a command the real environment can execute.
    try:
        from core import project_snapshot
        env_kind = ("node" if "node" in
                    str((commands.get("items") or {}).keys()).lower() else "python")
        allowed = project_snapshot.allowed_test_command(project, env_kind)
        verify_cmd = allowed.get("command") or ""
        env_note = allowed.get("note") or ""
    except Exception:  # noqa: BLE001 — fall back to structural command name
        allowed, env_note, verify_cmd = {}, "", ""
        for k in (commands.get("items") or {}):
            if k.startswith("npm:test") or k in ("python-entry", "static-preview"):
                verify_cmd = k
                break
    android = "android" in kind.lower()
    steps = [
        {"id": "inspect", "title": "بررسی وضعیت واقعی پروژه",
         "depends_on": [], "status": QUEUED, "attempts": 0, "strategy": "snapshot"},
        {"id": "implement", "title": "پیادهسازی تغییرات موردنیاز",
         "depends_on": ["inspect"], "status": QUEUED, "attempts": 0,
         "strategy": "generate"},
    ]
    if android:
        steps += [
            {"id": "build", "title": "ساخت بسته", "depends_on": ["implement"],
             "status": QUEUED, "attempts": 0, "strategy": "android-engine"},
            {"id": "verify", "title": "تأیید با ابزار واقعی", "depends_on": ["build"],
             "status": QUEUED, "attempts": 0, "strategy": "engine-verify"},
        ]
    else:
        steps += [
            {"id": "verify", "title": "اجرای تست واقعی", "depends_on": ["implement"],
             "status": QUEUED, "attempts": 0,
             "strategy": ("command:" + verify_cmd) if verify_cmd else
                         ("blocked:no-executable-test" if env_note else "testrun")},
            {"id": "repair", "title": "اصلاح مبتنی بر شکست واقعی",
             "depends_on": ["verify"], "status": SKIP, "attempts": 0,
             "strategy": "none"},
        ]
    steps.append({"id": "deliver", "title": "تحویل نهایی",
                  "depends_on": ["verify"], "status": QUEUED, "attempts": 0,
                  "strategy": "release"})
    plan = {
        "id": "plan-%d-%s" % (int(time.time() * 1000), project[-6:]),
        "project": str(project), "goal": str(goal)[:1000], "task_id": str(task_id),
        "type": kind, "created": time.time(), "updated": time.time(),
        "replans": 0, "steps": steps, "status": QUEUED,
        "decision": decision or {"decision": "PROCEED"},
        "project_model": {
            "status": snap.get("status"),
            "sections": list(sections.keys()),
            "entry": (sections.get("entry") or {}).get("path"),
            "commands": (sections.get("commands") or {}).get("items"),
            "environment": {k: (v or {}).get("value")
                            for k, v in caps.items()},
        },
        "environment_limits": [k for k, v in caps.items()
                               if not (v or {}).get("value")],
        "verify_command": verify_cmd,
    }
    if decision and decision.get("decision") in ("ASK", "CONFIRM"):
        plan["steps"][0]["status"] = BLOCKED
        plan["blocking_question"] = decision.get("question", "")
        plan["status"] = BLOCKED
    return _put(plan)


def next_step(plan: dict) -> dict | None:
    """First executable step whose dependencies are green/skipped."""
    # SKIP is never success: a skipped required step cannot unblock delivery.
    done = {s["id"] for s in plan["steps"] if s["status"] == PASS}
    for s in plan["steps"]:
        if s["status"] in (QUEUED,) and all(d in done for d in s.get("depends_on", [])):
            return s
    return None


def start(plan_id: str, step_id: str) -> dict | None:
    plan = get(plan_id)
    if not plan:
        return None
    for s in plan["steps"]:
        if s["id"] == step_id and s["status"] == QUEUED:
            s["status"] = RUNNING
            s["attempts"] += 1
            break
    plan["updated"] = time.time()
    return _put(plan)


def mark(plan_id: str, step_id: str, status: str,
         evidence: str = "") -> dict | None:
    plan = get(plan_id)
    if not plan or status not in (PASS, FAIL, BLOCKED, SKIP):
        return None
    for s in plan["steps"]:
        if s["id"] == step_id:
            s["status"] = status
            s["evidence"] = str(evidence)[:400]
            break
    plan["updated"] = time.time()
    status_all = {s["status"] for s in plan["steps"]}
    plan["status"] = (FAIL if FAIL in status_all else
                      BLOCKED if BLOCKED in status_all else
                      PASS if all(x == PASS for x in status_all)
                      else QUEUED)
    return _put(plan)


def answer(plan_id: str, reply: str) -> dict | None:
    """Unblock a plan after the user answers a material question."""
    plan = get(plan_id)
    if not plan or not plan.get("blocking_question"):
        return None
    plan.pop("blocking_question", None)
    plan["user_answer"] = str(reply)[:600]
    plan["status"] = QUEUED
    for s in plan["steps"]:
        if s["status"] == BLOCKED:
            s["status"] = QUEUED
    plan["updated"] = time.time()
    return _put(plan)


def replan(plan_id: str, evidence: str, failed_step: str = "",
           strategy: str | None = None) -> dict | None:
    """Replan ONLY with real evidence on a FAILED step; PASS steps are safe."""
    plan = get(plan_id)
    if not plan or plan.get("replans", 0) >= MAX_REPLANS:
        return None
    if not str(evidence or "").strip():
        return None                      # never replan on an empty reason
    failed = failed_step or next((s["id"] for s in plan["steps"]
                                  if s["status"] == FAIL), "")
    target = next((s for s in plan["steps"] if s["id"] == failed), None)
    if not target or target["status"] != FAIL:
        return None                      # a PASS/QUEUED step is not evidence
    strategies = ["narrow-scope", "alternate-tool", "inspect-first"]
    target["attempts"] = int(target.get("attempts") or 0) + 1
    idx = max(0, target["attempts"] - 1)
    target["strategy"] = str(strategy or strategies[min(idx, len(strategies) - 1)])[:80]
    target["status"] = QUEUED
    target["evidence"] = str(evidence)[:400]
    repair = next((s for s in plan["steps"] if s["id"] == "repair"), None)
    if repair:
        repair["status"] = QUEUED
        repair["strategy"] = target["strategy"]
        repair["evidence"] = str(evidence)[:400]
    plan["replans"] = plan.get("replans", 0) + 1
    plan["status"] = QUEUED
    plan["updated"] = time.time()
    plan.setdefault("history", []).append({
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "reason": str(evidence)[:300], "failed_step": failed,
        "new_strategy": target["strategy"]})
    return _put(plan)


def to_prompt(plan: dict | None) -> str:
    if not plan:
        return ""
    decision = plan.get("decision") or {}
    lines = ["### برنامهٔAgent (مراحل وابسته — وضعیت واقعی)"]
    if decision.get("assumption"):
        lines.append("فرض اعلامشده (کمخطر): " + str(decision["assumption"])[:240])
    if decision.get("question"):
        lines.append("سؤال تصمیم: " + str(decision["question"])[:240])
    model = plan.get("project_model") or {}
    if model:
        lines.append("شناخت پروژه: نوع=%s | ورود=%s | بخشها=%s" % (
            model.get("status"), model.get("entry") or "نامشخص",
            ",".join(model.get("sections") or [])[:120]))
        if model.get("commands"):
            lines.append("دستورات شناختهشده: " +
                         ",".join(list(model["commands"])[:8]))
    if plan.get("verify_command"):
        lines.append("دستور تست مجاز محیط: " + str(plan["verify_command"])[:120])
    if plan.get("environment_limits"):
        lines.append("محدودیت محیط: " + ",".join(plan["environment_limits"])[:160])
    selected = plan.get("selected_files") or []
    if selected:
        lines.append("فایلهای مرتبط (بر اساس شواهد کد): " + ",".join(selected[:8]))
    imp = plan.get("code_impact") or {}
    if imp:
        lines.append("تحلیل اثر: خطر=%s | وابسته=%s | تست=%s" % (
            (imp.get("risk") or {}).get("value", "unknown"),
            len((imp.get("direct") or {}).get("value") or []),
            len((imp.get("tests") or {}).get("value") or [])))
    for s in plan["steps"]:
        lines.append("- %s: %s [%s/%s]%s" % (
            s["id"], s.get("title", ""), s["status"], s.get("strategy", ""),
            (" — " + s["evidence"][:90]) if s.get("evidence") else ""))
    if plan.get("blocking_question"):
        lines.append("سؤال مانع: " + str(plan["blocking_question"])[:240])
    lines.append("### پایان برنامه")
    return "\n".join(lines)
