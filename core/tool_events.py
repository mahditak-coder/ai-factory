"""AF NEXT Phase 2 — durable ToolEvent ledger (independent of SSE/UI)."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path

try:  # pragma: no cover - POSIX (Termux/Linux) is the target platform
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX narrows the guarantee, fails closed
    fcntl = None

import core.config as config
from core import event_envelope, opstatus

_STORE = config.PROJECT_ROOT / "data" / "tool_events.json"
_LOCK = threading.RLock()
MAX_EVENTS = 600
# Durable idempotency receipts are intentionally SEPARATE from the bounded
# display/event history. Truncating history must never make a call replayable.
_RECEIPT_STORE = config.PROJECT_ROOT / "data" / "tool_receipts.json"
_RECEIPT_LOCK = threading.RLock()


class ReceiptStoreError(RuntimeError):
    """Raised when receipt integrity/availability cannot be established."""


def _unique_tmp(path: Path) -> Path:
    """Return a private temp path; no two writers ever share a temp filename."""
    return Path(str(path) + ".%d.%d.%s.tmp" %
                (os.getpid(), threading.get_ident(), secrets.token_hex(6)))


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _unique_tmp(path)
    try:
        tmp.write_text(text, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


@contextmanager
def _receipt_lock():
    """One critical section for threads AND independent processes.

    ``_RECEIPT_LOCK`` serialises threads inside this process; ``flock`` on a
    stable lock file serialises separate processes. Every receipt read/write
    and every state transition uses this same context manager. Lock failures
    fail closed — no adapter may be started without owning this lock.
    """
    with _RECEIPT_LOCK:
        if fcntl is None:
            raise ReceiptStoreError("receipt-lock-unavailable:fcntl-missing")
        lock_path = Path(str(_RECEIPT_STORE) + ".lock")
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            raise ReceiptStoreError(
                "receipt-lock-open-failed:%s" % type(exc).__name__) from exc
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError as exc:
                raise ReceiptStoreError(
                    "receipt-lock-acquire-failed:%s" % type(exc).__name__) from exc
            try:
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            os.close(fd)


def _load() -> list[dict]:
    try:
        d = json.loads(_STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _save(items: list[dict]) -> None:
    _atomic_write(_STORE,
                  json.dumps(items[-MAX_EVENTS:], ensure_ascii=False, indent=1))


def _marker_path() -> Path:
    return Path(str(_RECEIPT_STORE) + ".initialized")


def _write_marker() -> None:
    marker = _marker_path()
    if marker.exists():
        return
    _atomic_write(marker, "af-tool-receipts-1\n")


def _load_receipts() -> dict:
    """Return validated receipts or raise; never treat corruption as empty."""
    try:
        text = _RECEIPT_STORE.read_text(encoding="utf-8")
    except FileNotFoundError:
        if _marker_path().exists():
            raise ReceiptStoreError("receipt-store-missing-after-init")
        return {}
    except OSError as exc:
        raise ReceiptStoreError("receipt-store-unreadable:%s" % type(exc).__name__)
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        raise ReceiptStoreError("receipt-store-corrupt-json")
    if not isinstance(data, dict):
        raise ReceiptStoreError("receipt-store-invalid-schema")
    for key, value in data.items():
        if not key or not isinstance(value, dict) or value.get("call_id") != key:
            raise ReceiptStoreError("receipt-store-invalid-entry")
    return data


def _save_receipts(items: dict) -> None:
    try:
        _atomic_write(_RECEIPT_STORE,
                      json.dumps(items, ensure_ascii=False, indent=1))
        _write_marker()
    except OSError as exc:
        raise ReceiptStoreError("receipt-store-write-failed:%s" % type(exc).__name__) from exc


def initialize_receipt_store() -> dict:
    """Create a genuinely empty store once; never recreate a missing one."""
    with _receipt_lock():
        if _RECEIPT_STORE.exists():
            receipts = _load_receipts()
            _write_marker()
            return receipts
        if _marker_path().exists():
            raise ReceiptStoreError("receipt-store-missing-after-init")
        _save_receipts({})
        return {}


def claim(call_id: str, request_id: str, tool: str = "",
          project: str = "") -> dict:
    """Atomically read, check duplicate/signature and reserve a call_id.

    The full read -> duplicate check -> signature validation -> durable
    RESERVED write happens under one cross-process lock. The caller may start
    an adapter ONLY when ``claimed`` is True.

    Returns ``{"receipt": dict, "claimed": bool, "decision": str}`` where
    decision is one of ``ok``, ``mismatch``, ``unresolved``, ``duplicate``.
    """
    if not call_id:
        raise ReceiptStoreError("call-id-required")
    with _receipt_lock():
        receipts = _load_receipts()
        existing = receipts.get(call_id)
        if existing is not None:
            if str(existing.get("request_id") or "") != str(request_id or ""):
                decision = "mismatch"
            elif existing.get("lifecycle") in ("RESERVED", "IN_PROGRESS"):
                decision = "unresolved"
            else:
                decision = "duplicate"
            return {"receipt": existing, "claimed": False, "decision": decision}
        rec = {"call_id": call_id, "request_id": str(request_id or ""),
               "tool": str(tool or ""), "project": str(project or ""),
               "status": opstatus.NOT_TESTED, "lifecycle": "RESERVED",
               "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "event_uid": hashlib.sha256(
                   (call_id + "|" + str(request_id)).encode()).hexdigest()[:24]}
        receipts[call_id] = rec
        _save_receipts(receipts)
        return {"receipt": rec, "claimed": True, "decision": "ok"}


def reserve(call_id: str, request_id: str, tool: str = "",
            project: str = "") -> dict:
    """Backward-compatible RESERVED write; ``claim()`` is the atomic entrypoint."""
    return claim(call_id, request_id, tool, project)["receipt"]


def mark_in_progress(call_id: str) -> dict:
    """Record that the adapter is about to run; never rerun automatically."""
    with _receipt_lock():
        receipts = _load_receipts()
        rec = receipts.get(call_id)
        if rec is None:
            raise ReceiptStoreError("receipt-not-found:" + call_id)
        if rec.get("lifecycle") == "TERMINAL":
            return rec
        rec["lifecycle"] = "IN_PROGRESS"
        rec["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _save_receipts(receipts)
        return rec


def _finalize_unlocked(receipts: dict, call_id: str, event: dict) -> dict:
    """Write a terminal receipt into an already-loaded, lock-held dict.

    Caller MUST hold ``_receipt_lock()``. First terminal result wins; later
    terminal writes return the existing record instead of overwriting it.
    """
    if not call_id:
        return {}
    old = receipts.get(call_id)
    terminal_status = event_envelope.status(event.get("status"))
    if old and old.get("lifecycle") == "TERMINAL":
        return old
    rec = dict(old or {"call_id": call_id})
    rec.update({"call_id": call_id,
                "request_id": str(event.get("request_id") or rec.get("request_id") or ""),
                "tool": str(event.get("tool") or rec.get("tool") or ""),
                "project": str(event.get("project") or rec.get("project") or ""),
                "status": terminal_status,
                "lifecycle": "TERMINAL",
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "error": str(event.get("error") or "")[:300],
                "finished": event.get("finished"),
                "seq": event.get("seq"),
                "reconciled_by": str(event.get("reconciled_by") or
                                     rec.get("reconciled_by") or ""),
                "reconcile_reason": str(event.get("reconcile_reason") or
                                        rec.get("reconcile_reason") or ""),
                "reconciled_at": event.get("reconciled_at") or
                                 rec.get("reconciled_at")})
    receipts[call_id] = rec
    _save_receipts(receipts)
    return rec


def finalize(call_id: str, event: dict) -> dict:
    """Persist the terminal outcome under the cross-process lock; first wins."""
    if not call_id:
        return {}
    with _receipt_lock():
        receipts = _load_receipts()
        return _finalize_unlocked(receipts, call_id, event)


def unresolved_receipts() -> list[dict]:
    """Receipts requiring explicit reconciliation after a crash."""
    with _receipt_lock():
        return [r for r in _load_receipts().values()
                if r.get("lifecycle") in ("RESERVED", "IN_PROGRESS")]


def reconcile(call_id: str, status: str, actor: str, reason: str = "") -> dict:
    """Atomically reconcile one unresolved receipt with a full audit trail.

    The entire validate -> load -> reject -> persist sequence runs inside ONE
    ``_receipt_lock()`` critical section shared by threads and processes.
    Anonymous actors, non-terminal statuses, missing receipts and already
    terminal receipts are rejected. First terminal result wins: a competing
    reconciler gets an explicit ``already-reconciled`` error, never a silent
    overwrite. No adapter is rerun by this path.
    """
    with _receipt_lock():
        actor = str(actor or "").strip()
        if not actor:
            raise ReceiptStoreError("explicit-actor-required")
        if not event_envelope.is_terminal(status):
            raise ReceiptStoreError("terminal-status-required")
        receipts = _load_receipts()
        old = receipts.get(call_id)
        if old is None:
            raise ReceiptStoreError("reconcile-target-not-found:" + call_id)
        if old.get("lifecycle") == "TERMINAL":
            raise ReceiptStoreError("already-reconciled:" + call_id)
        return _finalize_unlocked(receipts, call_id, {
            "status": status, "request_id": "",
            "error": str(reason)[:300],
            "finished": time.time(),
            "reconciled_by": actor,
            "reconcile_reason": str(reason)[:300],
            "reconciled_at": time.strftime("%Y-%m-%dT%H:%M:%S")})


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
    return rec


def find(call_id: str) -> dict | None:
    """Return a durable receipt for *call_id*, if one exists."""
    if not call_id:
        return None
    with _receipt_lock():
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
