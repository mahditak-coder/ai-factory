"""AF NEXT Phase 2 — durable ToolEvent ledger (independent of SSE/UI)."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import core.config as config
from core import event_envelope, opstatus

_STORE = config.PROJECT_ROOT / "data" / "tool_events.json"
_LOCK = threading.RLock()
MAX_EVENTS = 600
# Durable idempotency receipts are intentionally SEPARATE from the bounded
# display/event history. Truncating history must never make a call replayable.
_RECEIPT_STORE = config.PROJECT_ROOT / "data" / "tool_receipts.json"
_RECEIPT_LOCK = threading.RLock()


def _load() -> list[dict]:
    try:
        d = json.loads(_STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _save(items: list[dict]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items[-MAX_EVENTS:], ensure_ascii=False, indent=1),
                   encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(_STORE)


def _load_receipts() -> dict:
    try:
        d = json.loads(_RECEIPT_STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_receipts(items: dict) -> None:
    _RECEIPT_STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _RECEIPT_STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(_RECEIPT_STORE)


def _remember_receipt(rec: dict) -> None:
    call_id = str(rec.get("call_id") or "")
    if not call_id:
        return
    with _RECEIPT_LOCK:
        receipts = _load_receipts()
        old = receipts.get(call_id)
        if old and event_envelope.is_terminal(old.get("status")) and \
                event_envelope.is_terminal(rec.get("status")):
            # First terminal result wins; never overwrite a real result with a
            # later replay/cancel marker.
            return
        receipts[call_id] = {
            "call_id": call_id,
            "request_id": str(rec.get("request_id") or ""),
            "status": str(rec.get("status") or opstatus.NOT_TESTED),
            "tool": str(rec.get("tool") or ""),
            "at": rec.get("at"),
            "seq": rec.get("seq"),
            "event_uid": rec.get("event_uid"),
            "error": str(rec.get("error") or "")[:300],
        }
        _save_receipts(receipts)


def record(event: dict) -> dict:
    rec = {
        "id": str(event.get("call_id") or event.get("id") or ""),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "call_id": str(event.get("call_id") or ""),
        "request_id": str(event.get("request_id") or ""),
        "tool": str(event.get("tool") or ""),
        "project": str(event.get("project") or ""),
        "task_id": str(event.get("task_id") or ""),
        "session_id": str(event.get("session_id") or ""),
        "decision": str(event.get("decision") or ""),
        "status": str(event.get("status") or "NOT TESTED"),
        "started": event.get("started"),
        "finished": event.get("finished"),
        "duration_ms": event.get("duration_ms"),
        "input": str(event.get("input") or "")[:2000],
        "output": str(event.get("output") or "")[:4000],
        "error": str(event.get("error") or "")[:1000],
        "evidence": event.get("evidence") or {},
    }
    rec = event_envelope.envelope(rec, kind="tool")
    with _LOCK:
        items = _load()
        idx = next((i for i, e in enumerate(items)
                    if e.get("call_id") == rec["call_id"] and rec["call_id"]), None)
        if idx is None:
            items.append(rec)
        else:
            old = items[idx]
            # First terminal result wins. A later replay/cancel must never
            # overwrite a real PASS/FAIL with BLOCKED.
            if event_envelope.is_terminal(old.get("status")) and \
                    event_envelope.is_terminal(rec.get("status")):
                merged = dict(old)
                evidence = dict(old.get("evidence") or {})
                evidence.update(rec.get("evidence") or {})
                merged["evidence"] = evidence
                rec = merged
            else:
                rec["seq"] = max(int(old.get("seq") or 0), int(rec["seq"]))
            items[idx] = rec
        _save(items)
    _remember_receipt(rec)
    return rec


def find(call_id: str) -> dict | None:
    """Return a durable receipt for *call_id*, if one exists."""
    if not call_id:
        return None
    with _RECEIPT_LOCK:
        receipt = _load_receipts().get(call_id)
    if receipt is not None:
        return receipt
    # Older records from before the separate receipt store remain discoverable
    # while they are still in the bounded event history.
    for item in reversed(_load()):
        if item.get("call_id") == call_id:
            return item
    return None


def events(project: str = "", tool: str = "", limit: int = 50) -> list[dict]:
    items = _load()
    if project:
        items = [e for e in items if e.get("project") == project]
    if tool:
        items = [e for e in items if e.get("tool") == tool]
    return list(reversed(items))[:limit]
