"""AF NEXT Phase 2 — governed Tool Execution Layer.

Lookup → validate inputs → SecurityPolicy → execute → verify → durable ToolEvent.
No tool runs without a policy decision, and no result is reported as PASS
unless the real adapter returned successfully.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from core import opstatus
from core.tool_events import record as record_event
from core.tool_registry import ToolSpec, registry


@dataclass
class ToolRequest:
    tool_id: str
    project: str
    inputs: dict[str, Any] = field(default_factory=dict)
    scopes: list[str] = field(default_factory=list)
    task_id: str = ""
    session_id: str = ""
    confirm_token: str = ""
    call_id: str = ""
    timeout_s: float | None = None


@dataclass
class ToolResult:
    status: str
    ok: bool = False
    output: dict | None = None
    error: str = ""
    decision: str = "none"
    call_id: str = ""
    duration_ms: int = 0
    evidence: dict = field(default_factory=dict)


def _authorize(spec: ToolSpec, req: ToolRequest) -> tuple[str, str]:
    """SecurityPolicy is the FINAL authority. Fail closed everywhere."""
    from core import boundary, paths
    if spec.scope not in (req.scopes or []):
        return "reject", "scope-not-granted"
    if spec.requires_confirm and not req.confirm_token:
        return "reject", "explicit-confirm-required"
    try:
        root = paths.project_dir(req.project).resolve()
    except (PermissionError, FileNotFoundError):
        return "reject", "project-not-found"
    if spec.scope == "project:write":
        rel = str(req.inputs.get("path") or "")
        if not rel or boundary.headless_permission("write", rel, req.project) != "once":
            return "reject", "write-outside-project"
    if spec.scope == "shell:execute":
        cmd = str(req.inputs.get("command") or "")
        if not cmd or boundary.headless_permission("shell", cmd, req.project) != "once":
            return "reject", "command-not-allowed"
    if spec.scope in ("media:write", "image:write", "image:generate"):
        rel = str(req.inputs.get("path") or req.inputs.get("output") or "")
        if rel and boundary.headless_permission(
                "write", rel, req.project) != "once":
            return "reject", "write-outside-project"
        if spec.scope != "image:generate" and not rel:
            return "reject", "write-outside-project"
    if spec.scope == "project:read":
        rel = str(req.inputs.get("path") or "")
        if rel:
            try:
                paths.project_path(req.project, rel)
            except (PermissionError, FileNotFoundError):
                return "reject", "path-outside-project"
    return "once", "policy-ok"


_seen_lock = threading.Lock()
_seen_calls: set[str] = set()
_running: dict[str, threading.Event] = {}


def execute(req: ToolRequest) -> ToolResult:
    call_id = req.call_id or ("tc-" + secrets.token_hex(8))
    started = time.time()
    spec = registry().get(req.tool_id)
    if spec is None:
        return _record(req, call_id, ToolResult(
            status=opstatus.BLOCKED, decision="none", call_id=call_id,
            error="tool-not-registered", duration_ms=0), started)
    if not spec.enabled:
        return _record(req, call_id, ToolResult(
            status=opstatus.BLOCKED, decision="none", call_id=call_id,
            error="tool-disabled", duration_ms=0), started)
    with _seen_lock:
        if call_id in _seen_calls:
            return _record(req, call_id, ToolResult(
                status=opstatus.BLOCKED, decision="none", call_id=call_id,
                error="duplicate-call", duration_ms=0), started)
        _seen_calls.add(call_id)
        if len(_seen_calls) > 2000:
            _seen_calls.clear()

    problem = spec.validate_inputs(req.inputs)
    if problem:
        return _record(req, call_id, ToolResult(
            status=opstatus.FAIL, decision="none", call_id=call_id,
            error="invalid-input: " + problem, duration_ms=0), started)

    decision, why = _authorize(spec, req)
    if decision != "once":
        return _record(req, call_id, ToolResult(
            status=opstatus.BLOCKED, decision=decision, call_id=call_id,
            error=why, duration_ms=0), started)

    cancel = threading.Event()
    with _seen_lock:
        _running[call_id] = cancel
    box: dict[str, Any] = {}
    timeout = float(req.timeout_s or spec.timeout_s)

    def runner():
        try:
            box["out"] = spec.adapter(req.project, req.inputs,
                                      timeout_s=timeout, cancel_event=cancel)
        except Exception as exc:  # noqa: BLE001 — real tool error, recorded
            box["err"] = "%s: %s" % (type(exc).__name__, exc)

    th = threading.Thread(target=runner, daemon=True,
                          name="tool-%s-%s" % (spec.id, call_id[:8]))
    th.start()
    th.join(timeout)
    if th.is_alive():
        cancel.set()
        with _seen_lock:
            _running.pop(call_id, None)
        return _record(req, call_id, ToolResult(
            status=opstatus.BLOCKED, decision=decision, call_id=call_id,
            error="tool-timeout", duration_ms=int((time.time()-started)*1000),
            evidence={"timeout_s": timeout}), started)
    with _seen_lock:
        _running.pop(call_id, None)
    if cancel.is_set():
        return _record(req, call_id, ToolResult(
            status=opstatus.BLOCKED, decision=decision, call_id=call_id,
            error="tool-cancelled", duration_ms=int((time.time()-started)*1000)), started)
    if "err" in box:
        return _record(req, call_id, ToolResult(
            status=opstatus.FAIL, decision=decision, call_id=call_id,
            error=box["err"][:600],
            duration_ms=int((time.time()-started)*1000)), started)
    evidence = {"validator": spec.output, "source": spec.source,
                "scope": spec.scope}
    return _record(req, call_id, ToolResult(
        status=opstatus.PASS, ok=True, output=box.get("out"),
        decision=decision, call_id=call_id,
        duration_ms=int((time.time()-started)*1000),
        evidence=evidence), started)


def cancel(call_id: str) -> bool:
    """Signal a RUNNING call, then record the real cancellation."""
    from core import tool_events
    with _seen_lock:
        evt = _running.get(call_id)
    if evt is None:
        return False
    evt.set()
    ev = next((e for e in tool_events.events(limit=600)
               if e.get("call_id") == call_id), None)
    if ev is None:
        return True
    tool_events.record({**ev, "status": opstatus.BLOCKED,
                        "error": "tool-cancelled", "finished": time.time()})
    return True


def _record(req: ToolRequest, call_id: str, result: ToolResult,
            started: float) -> ToolResult:
    finished = time.time()
    sig = hashlib.sha256((req.tool_id + "|" + req.project + "|" +
                          str(sorted(req.inputs.items(), key=lambda kv: str(kv[0]))))
                         .encode("utf-8")).hexdigest()[:16]
    record_event({"call_id": call_id,
                  "request_id": sig, "tool": req.tool_id,
                  "project": req.project, "task_id": req.task_id,
                  "session_id": req.session_id,
                  "decision": result.decision, "status": result.status,
                  "started": started, "finished": finished,
                  "duration_ms": result.duration_ms,
                  "input": str(req.inputs),
                  "output": str(result.output or ""),
                  "error": result.error, "evidence": result.evidence})
    return result
