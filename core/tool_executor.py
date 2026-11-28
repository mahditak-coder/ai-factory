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

from core import opstatus, tool_events
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
    # Phase 04: canonical role context for enforced tool authorization.
    role_id: str = ""
    enforce_role: bool = False
    # Exactly one identity is mandatory: canonical role contract OR a bounded
    # server-issued infrastructure capability. Caller strings are never trusted.
    capability_token: str = ""


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
_seen_sigs: dict[str, str] = {}
_running: dict[str, threading.Event] = {}


def _signature(req: ToolRequest) -> str:
    return hashlib.sha256((req.tool_id + "|" + req.project + "|" +
        str(sorted(req.inputs.items(), key=lambda kv: str(kv[0]))))
        .encode("utf-8")).hexdigest()[:16]


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
    # Phase 04 — MANDATORY identity at the real tool boundary.
    from core import tool_identity
    has_role = bool(str(req.role_id or ""))
    enforce = bool(req.enforce_role)
    has_cap = bool(str(req.capability_token or ""))
    if has_role != enforce:
        return _record(req, call_id, ToolResult(
            status=opstatus.BLOCKED, decision="none", call_id=call_id,
            error="incomplete-role-identity", duration_ms=0),
            started, finalize_receipt=False)
    if has_role and has_cap:
        return _record(req, call_id, ToolResult(
            status=opstatus.BLOCKED, decision="none", call_id=call_id,
            error="ambiguous-identity", duration_ms=0),
            started, finalize_receipt=False)
    if not has_role and not has_cap:
        return _record(req, call_id, ToolResult(
            status=opstatus.BLOCKED, decision="none", call_id=call_id,
            error="identity-required", duration_ms=0),
            started, finalize_receipt=False)
    if has_role:
        try:
            from core import specialist_engine as _se
            role_auth = _se.authorize_tool_request(
                req.role_id, spec.scope, req.session_id)
        except Exception as exc:  # noqa: BLE001 — fail closed
            role_auth = {"allowed": False,
                         "reason": "role-authorization-error:" +
                         type(exc).__name__}
        if not role_auth["allowed"]:
            return _record(req, call_id, ToolResult(
                status=opstatus.BLOCKED, decision="none", call_id=call_id,
                error="role-authorization-denied:" + str(role_auth["reason"]),
                duration_ms=0,
                evidence={"role_enforced": True, "role_id": req.role_id}),
                started, finalize_receipt=False)
    elif not tool_identity.validate_capability(
            req.capability_token, req.project, req.tool_id, spec.scope):
        return _record(req, call_id, ToolResult(
            status=opstatus.BLOCKED, decision="none", call_id=call_id,
            error="capability-invalid", duration_ms=0),
            started, finalize_receipt=False)
    sig = _signature(req)
    # One cross-process atomic operation: read, duplicate/signature check and
    # durable RESERVED write. An adapter may run only if this process won the
    # claim. _seen_lock still serialises threads inside this process.
    try:
        with _seen_lock:
            claim = tool_events.claim(call_id, sig, req.tool_id, req.project)
            claimed = claim["claimed"]
            if claimed:
                tool_events.mark_in_progress(call_id)
                _seen_calls.add(call_id)
                _seen_sigs[call_id] = sig
    except tool_events.ReceiptStoreError as exc:
        return _record(req, call_id, ToolResult(
            status=opstatus.BLOCKED, decision="none", call_id=call_id,
            error="receipt-store-unavailable:" + str(exc)[:160], duration_ms=0),
            started, finalize_receipt=False)
    if not claimed:
        error = {"mismatch": "duplicate-call-signature-mismatch",
                 "unresolved": "unresolved-in-progress:explicit-recovery-required",
                 "duplicate": "duplicate-call"}.get(claim["decision"],
                                                    "duplicate-call")
        return _record(req, call_id, ToolResult(
            status=opstatus.BLOCKED, decision="none", call_id=call_id,
            error=error, duration_ms=0,
            evidence={"replayed": True, "durable": True,
                      "lifecycle": claim["receipt"].get("lifecycle")}), started,
            finalize_receipt=False)

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
            evidence={"timeout_s": timeout, "cancel_requested": True,
                      "cancel_acknowledged": False}), started)
    with _seen_lock:
        _running.pop(call_id, None)
    if cancel.is_set():
        return _record(req, call_id, ToolResult(
            status=opstatus.BLOCKED, decision=decision, call_id=call_id,
            error="tool-cancelled", duration_ms=int((time.time()-started)*1000),
            evidence={"cancel_requested": True, "cancel_acknowledged": True}), started)
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
    """Signal a RUNNING call and record an observable cancellation request."""
    with _seen_lock:
        evt = _running.get(call_id)
    if evt is None:
        return False
    tool_events.record({
        "call_id": "cancel-" + call_id, "tool": "tool.cancel",
        "status": opstatus.BLOCKED, "decision": "cancel",
        "error": "cancel-requested", "finished": time.time(),
        "evidence": {"target_call_id": call_id}})
    evt.set()
    return True


def state(call_id: str) -> dict:
    """Return observable execution/cancellation state for *call_id*."""
    with _seen_lock:
        evt = _running.get(call_id)
    running = evt is not None
    request = tool_events.find("cancel-" + call_id)
    result = tool_events.find(call_id)
    return {"call_id": call_id, "running": running,
            "cancel_requested": bool(request or (evt and evt.is_set())),
            "cancel_acknowledged": bool(result and result.get("error") ==
                                        "tool-cancelled"),
            "status": (result or {}).get("status", "NOT TESTED")}


def _record(req: ToolRequest, call_id: str, result: ToolResult,
             started: float, *, finalize_receipt: bool = True) -> ToolResult:
    finished = time.time()
    sig = _signature(req)
    event = {"call_id": call_id,
             "request_id": sig, "tool": req.tool_id,
             "project": req.project, "task_id": req.task_id,
             "session_id": req.session_id,
             "decision": result.decision, "status": result.status,
             "started": started, "finished": finished,
             "duration_ms": result.duration_ms,
             "input": str(req.inputs),
             "output": str(result.output or ""),
             "error": result.error, "evidence": result.evidence}
    if finalize_receipt:
        try:
            tool_events.finalize(call_id, event)
        except tool_events.ReceiptStoreError as exc:
            # Fail closed: no terminal receipt means the call stays unresolved
            # and every replay is rejected until explicit reconciliation.
            result.status = opstatus.BLOCKED
            result.ok = False
            result.error = "receipt-finalize-failed:" + str(exc)[:160]
            event["status"] = opstatus.BLOCKED
            event["error"] = result.error
    record_event(event)
    return result
