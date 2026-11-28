"""AF — approval, cost and usage governance for PAID image generation.

No provider call is permitted without an explicit, one-shot approval bound to
the exact request (prompt, references, model, quality, cost). Cost must be
estimated from configured values; unknown cost is always rejected. No timeout
or automatic approval exists. Usage is recorded after the call only.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

import core.config as config
from core import opstatus

_CONFIG = config.PROJECT_ROOT / "config" / "image_providers.json"
_STORE = config.PROJECT_ROOT / "data" / "image_usage.json"
_LOCK = threading.RLock()

CRITICAL = "Critical"


def _cfg() -> dict:
    try:
        return json.loads(_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _load() -> dict:
    try:
        d = json.loads(_STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {"approvals": {}, "usage": []}


def _save(data: dict) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(_STORE)


_REQUEST_KEYS = ("project", "provider", "model", "quality", "prompt", "size",
                 "references", "operation")


def payload_from(project: str, req: dict) -> dict:
    out = {k: req.get(k) for k in _REQUEST_KEYS}
    out["project"] = project
    return out


def request_hash(payload: dict) -> str:
    blob = json.dumps({k: payload.get(k) for k in _REQUEST_KEYS},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def estimate(provider: str, model: str, quality: str = "standard") -> dict:
    """Return a configured estimate. Never invent a price; unknown => reject."""
    for entry in _cfg().get("providers", []):
        if entry.get("id") == provider:
            models = entry.get("models") or {}
            info = models.get(model) or {}
            value = info.get("estimated_cost_usd") if isinstance(info, dict) else None
            if value is None:
                value = entry.get("estimated_cost_usd")
            if value is None:
                return {"ok": False, "error": "cost-estimate-unavailable",
                        "reason": "no configured estimate; set from current "
                                  "provider pricing before any paid call"}
            try:
                amount = round(float(value) * float(
                    (info.get("quality_multiplier") or {}).get(quality, 1)
                    if isinstance(info, dict) else 1), 4)
            except (TypeError, ValueError):
                return {"ok": False, "error": "cost-estimate-invalid"}
            return {"ok": True, "amount_usd": amount,
                    "free": bool(amount == 0 and entry.get("free_cost_model")),
                    "source": (info.get("cost_source") if isinstance(info, dict)
                               else None) or "operator-configured"}
    return {"ok": False, "error": "provider-not-configured"}


def limits() -> dict:
    cfg = _cfg()
    return {"per_request_usd": float(cfg.get("spending", {}).get(
                "per_request_usd") or 0),
            "daily_usd": float(cfg.get("spending", {}).get("daily_usd") or 0)}


def spent_today() -> float:
    today = time.strftime("%Y-%m-%d")
    with _LOCK:
        data = _load()
    return round(sum(float(e.get("cost_usd") or 0) for e in data.get("usage", [])
                     if str(e.get("at", "")).startswith(today)), 4)


APPROVAL_TTL_SECONDS = 900  # 15 minutes; expired approvals are never consumed


def approval_info(approval_id: str) -> dict:
    """READ-ONLY inspection of one approval, including expiry. No mutation."""
    aid = str(approval_id or "")
    if not aid:
        return {"ok": False, "error": "approval-id-required"}
    with _LOCK:
        rec = _load().get("approvals", {}).get(aid)
    if not isinstance(rec, dict):
        return {"ok": False, "error": "unknown-approval"}
    try:
        at = time.strptime(str(rec.get("at") or ""), "%Y-%m-%dT%H:%M:%S")
        age = max(0.0, time.time() - time.mktime(at))
    except (ValueError, OverflowError):
        age = float(APPROVAL_TTL_SECONDS + 1)
    out = dict(rec)
    out.update({"ok": True, "age_seconds": round(age, 2),
                "expired": age > APPROVAL_TTL_SECONDS,
                "ttl_seconds": APPROVAL_TTL_SECONDS})
    return out


def request_approval(project: str, payload: dict, actor: str = "") -> dict:
    """Create a pending approval; it NEVER auto-grants anything."""
    est = estimate(str(payload.get("provider") or ""),
                   str(payload.get("model") or ""),
                   str(payload.get("quality") or "standard"))
    if not est.get("ok"):
        return {"status": opstatus.BLOCKED, "error": est.get("error"),
                "reason": est.get("reason", "")}
    lim = limits()
    cost = est["amount_usd"]
    if cost < 0 or (cost == 0 and not est.get("free")) or \
            cost > lim["per_request_usd"]:
        return {"status": opstatus.BLOCKED, "error": "per-request-limit-exceeded",
                "cost_usd": cost, "limit_usd": lim["per_request_usd"]}
    if spent_today() + cost > lim["daily_usd"]:
        return {"status": opstatus.BLOCKED, "error": "daily-limit-exceeded",
                "cost_usd": cost, "spent_today_usd": spent_today(),
                "limit_usd": lim["daily_usd"]}
    rh = request_hash(payload)
    aid = "ap-img-" + hashlib.sha256(
        (rh + str(time.time())).encode()).hexdigest()[:16]
    with _LOCK:
        data = _load()
        data.setdefault("approvals", {})[aid] = {
            "id": aid, "project": project, "request_hash": rh,
            "provider": payload.get("provider"), "model": payload.get("model"),
            "quality": payload.get("quality"), "estimated_cost_usd": cost,
            "actor": str(actor)[:80], "decision": "pending",
            "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        _save(data)
    return {"status": opstatus.PASS, "approval_id": aid, "request_hash": rh,
            "estimated_cost_usd": cost, "decision_required": True}


def decide(approval_id: str, decision: str, actor: str = "") -> dict:
    """Explicit human decision: 'approve' or 'reject'. No default approval."""
    if str(decision).strip().lower() not in ("approve", "reject"):
        return {"status": opstatus.FAIL, "error": "invalid-decision"}
    with _LOCK:
        data = _load()
        rec = data.get("approvals", {}).get(approval_id)
        if rec is None:
            return {"status": opstatus.BLOCKED, "error": "unknown-approval"}
        if rec.get("decision") != "pending":
            return {"status": opstatus.BLOCKED, "error": "approval-not-pending"}
        rec["decision"] = "approved" if decision == "approve" else "rejected"
        rec["decided_by"] = str(actor)[:80]
        rec["decided_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _save(data)
    return {"status": opstatus.PASS, "decision": rec["decision"]}


def consume(approval_id: str, payload: dict) -> dict:
    """One-shot: verify exact request binding and mark the approval spent."""
    with _LOCK:
        data = _load()
        rec = data.get("approvals", {}).get(approval_id)
        if rec is None:
            return {"status": opstatus.BLOCKED, "error": "unknown-approval"}
        if rec.get("decision") != "approved":
            return {"status": opstatus.BLOCKED, "error": "approval-not-granted"}
        if rec.get("consumed"):
            return {"status": opstatus.BLOCKED, "error": "approval-already-used"}
        if rec.get("request_hash") != request_hash(payload):
            return {"status": opstatus.FAIL, "error": "approval-request-mismatch"}
        if spent_today() + float(rec["estimated_cost_usd"]) > limits()["daily_usd"]:
            return {"status": opstatus.BLOCKED, "error": "daily-limit-exceeded"}
        rec["consumed"] = True
        _save(data)
    return {"status": opstatus.PASS, "ok": True,
            "cost_usd": float(rec["estimated_cost_usd"])}


def record_usage(approval_id: str, provider: str, model: str,
                 actual: dict | None = None) -> dict:
    """Records real provider usage when available; never claims a guarantee."""
    with _LOCK:
        data = _load()
        rec = data.get("approvals", {}).get(approval_id) or {}
        entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "approval_id": approval_id, "provider": provider, "model": model,
                 "cost_usd": float(rec.get("estimated_cost_usd") or 0),
                 "cost_basis": "estimate", "usage": actual or {},
                 "guarantee": "none — provider billing is authoritative"}
        data.setdefault("usage", []).append(entry)
        data["usage"] = data["usage"][-1000:]
        _save(data)
    return entry
