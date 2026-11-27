"""AF NEXT Rebuild Phase 01 — canonical event envelope.

One small, dependency-free helper shared by the durable ToolEvent and
AgentEvent ledgers. It adds identity, ordering and schema fields without
removing legacy keys, so existing Workspace/SSE consumers keep working.
"""
from __future__ import annotations

import hashlib
import json
import time

import core.opstatus as opstatus

SCHEMA_VERSION = "af-event-1.0"
TERMINAL = frozenset((opstatus.PASS, opstatus.FAIL, opstatus.BLOCKED,
                      opstatus.SKIP))


def status(value: object) -> str:
    """Normalize any event outcome into the canonical status vocabulary."""
    text = str(value or "").strip()
    upper = text.upper()
    if upper in opstatus.STATUSES:
        return upper
    if upper in ("SUCCESS", "SUCCEEDED", "COMPLETED", "OK", "DONE"):
        return opstatus.PASS
    if upper in ("ERROR", "FAILED", "FAILURE"):
        return opstatus.FAIL
    if upper in ("CANCELLED", "CANCELED", "DENIED", "REJECTED", "TIMEOUT"):
        return opstatus.BLOCKED
    if upper in ("SKIPPED",):
        return opstatus.SKIP
    return opstatus.NOT_TESTED


def is_terminal(value: object) -> bool:
    return status(value) in TERMINAL


def envelope(record: dict, *, kind: str = "event") -> dict:
    """Return a COPY of *record* enriched with canonical envelope fields."""
    out = dict(record or {})
    out["schema_version"] = SCHEMA_VERSION
    out["kind"] = str(out.get("kind") or kind)
    out["status"] = status(out.get("status"))
    out["seq"] = int(out.get("seq") or time.time_ns())
    out["event_uid"] = str(out.get("event_uid") or _uid(out))
    return out


def _uid(record: dict) -> str:
    raw = json.dumps({k: record.get(k) for k in (
        "id", "call_id", "event_type", "kind", "status", "at", "seq")},
        ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def validate(record: dict) -> str | None:
    """Return None when *record* satisfies the canonical envelope contract."""
    required = ("event_uid", "schema_version", "seq", "kind", "status", "at")
    missing = [key for key in required if not record.get(key)]
    if missing:
        return "missing-envelope-fields:" + ",".join(missing)
    if record.get("schema_version") != SCHEMA_VERSION:
        return "unsupported-schema-version"
    if record.get("status") not in opstatus.STATUSES:
        return "invalid-status"
    return None
