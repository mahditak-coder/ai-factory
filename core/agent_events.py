"""AF-29 — ToolEventRecorder + DecisionLog.

Records REAL OpenCode events (tool calls/results/errors, permission requests and
decisions) into one bounded, atomic 0600 store and feeds the observed outcomes
back into the next model decision.  Nothing here invents a tool result: when a
result is absent the status is NOT TESTED and the reason is explicit.

The only source of events is the live SSE stream in ``server._api_oc_events``
and the permission API.  Unit tests may feed representative event dicts, but
those are labelled as unit tests, never as end-to-end proof.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import core.config as config
from core import errreason, event_envelope, opstatus

_STORE = config.PROJECT_ROOT / "data" / "agent_events.json"
_LOCK = threading.RLock()
MAX_EVENTS = 600
MAX_TEXT = 2400
MAX_RAW = 4000
MAX_PROMPT_CHARS = 1400

_SUCCESS = {"completed", "success", "succeeded", "ok", "done"}
_FAILURE = {"error", "failed", "failure", "aborted", "interrupted", "rejected"}
_PROGRESS = {"pending", "running", "in_progress", "started", "ask", "waiting"}


def _load() -> list[dict]:
    try:
        d = json.loads(_STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _save(events: list[dict]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(events[-MAX_EVENTS:], ensure_ascii=False, indent=1),
                   encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(_STORE)


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False,
                                                        default=str)
    return errreason.redact(s)[:limit]


def _status_of(raw: Any) -> str:
    word = str(raw or "").lower()
    if word in _SUCCESS:
        return opstatus.PASS
    if word in _FAILURE:
        return opstatus.FAIL
    if word in _PROGRESS:
        return opstatus.NOT_TESTED
    return opstatus.NOT_TESTED        # unknown is NEVER success


def _event_id(evt: dict) -> str:
    return str(evt.get("id") or evt.get("eventID") or
               "%d-%s" % (int(time.time() * 1000),
                          str(evt.get("type", ""))[-12:]))


def record(evt: dict, project: str = "", session: str = "",
           task_id: str = "") -> dict:
    """Normalize one real event into a structured, redacted record."""
    etype = str(evt.get("type") or "unknown")
    data = evt.get("data") if isinstance(evt.get("data"), dict) else {}
    part = data.get("part") if isinstance(data.get("part"), dict) else {}
    state = (data.get("state") if isinstance(data.get("state"), dict)
             else part.get("state") if isinstance(part.get("state"), dict) else {})
    raw_json = json.dumps(evt, ensure_ascii=False, default=str)
    # AF-36 — deterministic ids make SSE and backend recording idempotent.
    kind_hint = "tool" if etype.startswith("session.tool.") else (
        "permreq" if etype == "permission.asked" else "")
    deterministic = ""
    if kind_hint == "tool":
        deterministic = "tool-%s-%s" % (data.get("id") or "", _status_of(
            "completed" if etype == "session.tool.success" else
            "error" if etype in ("session.tool.error", "session.tool.failed")
            else "running"))
    elif kind_hint == "permreq":
        deterministic = "permreq-%s" % (data.get("id") or "")
    rec: dict = {
        "id": deterministic or _event_id(evt),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "event_type": etype,
        "project": str(project or data.get("projectID") or ""),
        "session_id": str(session or data.get("sessionID") or ""),
        "task_id": str(task_id or ""),
        "kind": "event",
        "tool": str(data.get("toolName") or part.get("tool") or ""),
        "call_id": str(data.get("toolCallID") or part.get("callID") or
                       data.get("id") or ""),
        "agent": str(data.get("agent") or part.get("agent") or ""),
        "status": opstatus.NOT_TESTED,
        "detail": "",
        "input": "",
        "output": "",
        "error": "",
        "files_changed": [],
        "metadata": None,
        "resources": None,
        "diff": "",
        "raw_truncated": len(raw_json) > MAX_RAW,
        "raw": _text(raw_json, MAX_RAW),
    }

    # AF-30 — normalized against the REAL OpenCode 2.2.6 event schema observed
    # on a live stream (session.tool.*, permission.asked/replied).
    if etype.startswith("session.tool."):
        rec["kind"] = "tool"
        rec["call_id"] = str(data.get("id") or "")
        rec["tool"] = str(data.get("name") or "")
        if etype == "session.tool.input.ended" and data.get("text"):
            rec["input"] = _text(data["text"])
        elif isinstance(data.get("input"), (dict, list)):
            rec["input"] = _text(data["input"])
        if etype == "session.tool.success":
            content = data.get("content") or []
            output = "\n".join(str(p.get("text", "")) for p in content
                               if isinstance(p, dict))
            rec["output"] = _text(output)
            rec["error"] = _text(data.get("error"))
            meta = data.get("metadata")
            if isinstance(meta, dict):
                rec["metadata"] = {str(k): _text(v, 600) for k, v in meta.items()}
            # A "tool.success" event can still carry a FAILED shell command.
            # The real exit code is in the content ("Exited with code N").
            import re as _re
            m = _re.search(r"Exited with code\s+(\d+)", output, _re.I)
            exit_code = str((meta or {}).get("exit")) if isinstance(meta, dict) else ""
            failed = ((m and int(m.group(1)) != 0) or bool(data.get("error")) or
                      (exit_code not in ("", "0", "None", "none")))
            rec["status"] = opstatus.FAIL if failed else opstatus.PASS
            if failed:
                rec["error"] = rec["error"] or _text(output, MAX_TEXT)
        elif etype in ("session.tool.error", "session.tool.failed"):
            rec["status"] = opstatus.FAIL
            content = data.get("content") or []
            real_err = data.get("error") or data.get("message")
            if not real_err and content:
                real_err = "\n".join(str(p.get("text", "")) for p in content
                                     if isinstance(p, dict))
            rec["error"] = _text(real_err or etype)
            rec["detail"] = _text(etype) + " — شکست واقعی ابزار"
        else:
            rec["status"] = opstatus.NOT_TESTED   # started/called ≠ result
            rec["detail"] = _text(etype) + " — نتیجهای ثبت نشد"
    elif etype == "permission.asked":
        rec.update({
            "kind": "permission.request",
            "status": opstatus.BLOCKED,          # awaiting a real decision
            "call_id": str(data.get("id") or ""),
            "tool": str(data.get("action") or ""),
            "detail": _text(" ".join(data.get("resources") or []) or
                            str(data.get("resource") or "")),
            "agent": str((data.get("source") or {}).get("type") or ""),
            "metadata": {"save": data.get("save"), "source": data.get("source")},
            "resources": ([_text(x, 300) for x in data["resources"][:20]]
                          if isinstance(data.get("resources"), list) else None),
            "diff": _text(data.get("diff")),
        })
    elif etype == "permission.replied":
        rec.update({"kind": "permission.decision",
                    "call_id": str(data.get("requestID") or ""),
                    "tool": "permission",
                    "status": (opstatus.PASS if data.get("reply") in ("once", "always")
                               else opstatus.BLOCKED),
                    "detail": "reply=" + str(data.get("reply")),
                    "metadata": {"actor": "panel"}})
    elif etype in ("session.error", "session.cancelled", "session.interrupted",
                   "session.execution.failed"):
        rec.update({"kind": "turn.error", "status": opstatus.FAIL,
                    "detail": _text(data.get("error") or etype, 600)})
    elif etype == "session.idle":
        rec.update({"kind": "turn.end", "status": opstatus.SKIP,
                    "detail": "پایان نوبت — مدرک موفقیت ابزار نیست"})
    else:
        rec["detail"] = _text(data.get("error") or data.get("title") or etype, 300)

    rec = event_envelope.envelope(rec, kind=rec.get("kind") or "event")
    with _LOCK:
        events = _load()
        # Same real event may arrive from SSE and from the backend recorder.
        existing = next((e for e in events if e.get("id") == rec["id"]), None)
        if existing is not None:
            if rec["status"] != opstatus.NOT_TESTED:
                existing.update({"status": rec["status"],
                                 "output": rec["output"] or existing.get("output", ""),
                                 "error": rec["error"] or existing.get("error", ""),
                                 "metadata": rec["metadata"] or existing.get("metadata")})
                _save(events)
            return existing
        # Merge a real result into its earlier call record (same call/session).
        if rec["kind"] == "tool" and rec["call_id"]:
            for old in reversed(events):
                if (old.get("kind") == "tool" and
                        old.get("call_id") == rec["call_id"] and
                        old.get("session_id") == rec["session_id"]):
                    if rec["tool"] and not old.get("tool"):
                        old["tool"] = rec["tool"]
                    if rec["input"] and not old.get("input"):
                        old["input"] = rec["input"]
                    if etype in ("session.tool.success", "session.tool.error",
                                 "session.tool.failed"):
                        old.update({"status": rec["status"],
                                    "event_type": etype,
                                    "detail": _text(etype) +
                                    (" — شکست واقعی ابزار" if rec["status"] == opstatus.FAIL
                                     else " — نتیجهٔ واقعی ابزار"),
                                    "output": rec["output"] or old.get("output", ""),
                                    "error": rec["error"] or old.get("error", ""),
                                    "metadata": rec["metadata"] or old.get("metadata")})
                        _save(events)
                        return old
                    _save(events)
                    return old
        events.append(rec)
        _save(events)
    return rec


def record_permission_decision(session: str, request_id: str, decision: str,
                               actor: str = "panel", request: dict | None = None) -> dict:
    rec = {
        "id": "perm-decision-%s-%s" % (request_id, int(time.time() * 1000)),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "event_type": "af.permission.decision",
        "session_id": str(session),
        "call_id": str(request_id),
        "kind": "permission.decision",
        "tool": "permission",
        "status": (opstatus.PASS if decision in ("once", "always")
                   else opstatus.BLOCKED if decision == "reject" else opstatus.FAIL),
        "detail": "%s by %s" % (decision, actor),
        "agent": str((request or {}).get("agent") or ""),
        "input": "", "output": "", "error": "",
        "files_changed": [],
        "metadata": {"actor": actor, "decision": decision,
                     "request_metadata": (request or {}).get("metadata")},
        "resources": (request or {}).get("resources"),
        "diff": _text((request or {}).get("diff")),
        "raw": "", "raw_truncated": False,
        "task_id": "",
    }
    with _LOCK:
        events = _load()
        # AF-35 — the API reply and the SSE `permission.replied` event both
        # report the same decision. Keep ONE record per request.
        duplicate = next((e for e in events
                          if e.get("kind") in ("permission.decision", "permission.request")
                          and e.get("call_id") == rec["call_id"]), None)
        if duplicate is not None:
            duplicate.update(rec)
            rec = duplicate
        else:
            events.append(rec)
        _save(events)
    return rec


def record_policy_rejection(session: str, request_id: str, project: str = "",
                            task_id: str = "", action: str = "",
                            resource=None, explanation: dict | None = None) -> dict:
    """AF-44 — record a REAL policy rejection as non-success evidence.

    A rejection is BLOCKED, never PASS.  The record keeps the exact reason,
    forbidden target, project scope and safe alternatives so the next model
    decision (and the orchestrator) can recover in the SAME task/session.
    """
    import hashlib
    exp = explanation or {}
    resources = ([str(x) for x in resource] if isinstance(resource, (list, tuple))
                 else [str(resource or "")])
    detail = "reason=%s | forbidden=%s | scope=%s | limit=%s" % (
        exp.get("reason", "unknown"),
        ", ".join(str(x) for x in (exp.get("forbidden") or resources))[:200],
        str(exp.get("scope", ""))[:160], str(exp.get("policy_limit", ""))[:180])
    rec = {
        "id": "policy-rejection-%s" % request_id,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "event_type": "af.policy.rejection",
        "project": str(project),
        "session_id": str(session),
        "task_id": str(task_id),
        "kind": "policy.rejection",
        "tool": "permission",
        "call_id": str(request_id),
        "agent": "headless-policy",
        "status": opstatus.BLOCKED,
        "detail": _text(detail, 700),
        "input": _text(action + " " + " ".join(resources)),
        "output": "", "error": "", "files_changed": [],
        "metadata": {"actor": "headless-policy", "decision": "reject",
                     "reason": exp.get("reason", "unknown"),
                     "forbidden": exp.get("forbidden") or resources[:8],
                     "scope": exp.get("scope", ""),
                     "policy_limit": exp.get("policy_limit", ""),
                     "alternatives": exp.get("alternatives") or [],
                     "signature": exp.get("signature") or
                     hashlib.sha256(detail.encode("utf-8")).hexdigest()[:16]},
        "resources": resources[:20], "diff": "",
        "raw": "", "raw_truncated": False,
    }
    with _LOCK:
        events = _load()
        duplicate = next((e for e in events if e.get("id") == rec["id"]), None)
        if duplicate is not None:
            duplicate.update(rec)
            rec = duplicate
        else:
            events.append(rec)
        _save(events)
    return rec


def policy_rejection_stats(project: str = "", session: str | None = None,
                           task_id: str | None = None) -> dict:
    """Honest counts/signatures of REAL rejections — for loop control."""
    with _LOCK:
        items = _load()
    if project:
        items = [e for e in items if e.get("project") == project]
    if session:
        items = [e for e in items if e.get("session_id") == session]
    if task_id:
        items = [e for e in items if e.get("task_id") == task_id]
    items = [e for e in items if e.get("kind") == "policy.rejection"]
    signatures = [str(((e.get("metadata") or {}).get("signature")) or "")
                  for e in items]
    return {"total": len(items),
            "distinct": len(set(s for s in signatures if s)),
            "signatures": signatures,
            "latest": items[-1] if items else None}


def events(project: str | None = None, session: str | None = None,
           task_id: str | None = None, limit: int = 50) -> list[dict]:
    with _LOCK:
        out = _load()
    if project:
        out = [e for e in out if e.get("project") == project]
    if session:
        out = [e for e in out if e.get("session_id") == session]
    if task_id:
        out = [e for e in out if e.get("task_id") == task_id]
    return list(reversed(out))[:limit]


def decision_prompt(project: str = "", session: str | None = None,
                    task_id: str | None = None,
                    max_chars: int = MAX_PROMPT_CHARS) -> str:
    """Observed tool outcomes for the NEXT decision — real data only."""
    items = events(project, session, task_id, limit=120)
    items = [e for e in items if e.get("kind") in (
        "tool", "turn.error", "permission.request", "permission.decision",
        "policy.rejection")]
    # Terminal outcomes first; dedupe by call so a start event cannot evict a
    # real FAIL/PASS result from the bounded decision window.
    rank = {opstatus.FAIL: 0, opstatus.BLOCKED: 1, opstatus.PASS: 2,
            opstatus.NOT_TESTED: 3, opstatus.SKIP: 4}
    items.sort(key=lambda e: str(e.get("at") or ""), reverse=True)
    items.sort(key=lambda e: rank.get(e.get("status"), 5))
    seen_calls: set[str] = set()
    picked = []
    for e in items:
        cid = str(e.get("call_id") or e.get("id") or "")
        if cid and cid in seen_calls:
            continue
        if cid:
            seen_calls.add(cid)
        picked.append(e)
        if len(picked) >= 24:
            break
    if not picked:
        return ("### مشاهدات ابزار (واقعی)\n"
                "هیچ رویداد ابزاری ثبت نشده است — هیچ نتیجهای فرض نکن.\n"
                "### پایان مشاهدات")
    lines = ["### مشاهدات ابزار (واقعی — نه حدس)"]
    for e in picked:
        label = e.get("tool") or e.get("kind")
        line = "- %s: %s | %s" % (e.get("status"), label,
                                  errreason.redact(str(e.get("detail", "")))[:140])
        if e.get("error"):
            line += " | خطا: " + errreason.redact(str(e["error"]))[:180]
        elif e.get("output"):
            line += " | خروجی: " + errreason.redact(str(e["output"]))[:180]
        if e.get("files_changed"):
            line += " | فایلها: " + ", ".join(e["files_changed"][:5])
        lines.append(line)
    # AF-44 — rejections are real non-success evidence.  Surface the exact
    # reason/forbidden path and demand a different, safe action.
    rejects = [e for e in picked if e.get("kind") == "policy.rejection"]
    if rejects:
        lines.append("### ردهای سیاست (واقعی — موفقیت نیست)")
        for e in rejects[:6]:
            meta = e.get("metadata") or {}
            alts = meta.get("alternatives") or []
            lines.append("- BLOCKED: %s | علت: %s | ممنوع: %s | جایگزین امن: %s" % (
                errreason.redact(str(e.get("detail", "")))[:150],
                str(meta.get("reason") or "unknown")[:60],
                ", ".join(str(x) for x in (meta.get("forbidden") or []))[:100],
                "; ".join(str(x) for x in alts)[:160]))
        lines.append("دستور ردشده را تکرار نکن؛ در همان نشست از مسیر امن پروژه استفاده کن.")
    lines.append("### پایان مشاهدات")
    text = "\n".join(lines)
    return text[:max_chars] if len(text) <= max_chars else \
        text[:max_chars - 40] + "\n### پایان مشاهدات (برش خورده)"


def stats() -> dict:
    out = {s: 0 for s in opstatus.STATUSES}
    for e in _load():
        st = e.get("status")
        if st in out:
            out[st] += 1
    return {"counts": out, "total": len(_load())}
