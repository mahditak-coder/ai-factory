"""AF NEXT Phase 06 — ADR-012 user-controlled provider policy gate.

The user approved a hybrid policy:
  * local Pillow/FFmpeg tools stay available;
  * remote providers may expose free AND paid models;
  * the user explicitly selects provider AND model for every workflow —
    there is no default, no ``active()`` fallback and no auto-switching;
  * free models may run without payment approval, but NEVER without selection;
  * paid models require a configured exact estimate plus an explicit one-shot
    approval consumed immediately before the single provider call;
  * credentials are read ONLY from environment variables (names only stored);
  * availability and price are verified before execution;
  * no retry, no paid fallback, no second provider.

This module is a GATE, not an executor: it never contacts a network by itself.
"""
from __future__ import annotations

import json
import os

import core.config as config
from core import image_governance as gov
from core import image_providers as ip
from core import opstatus

_CONFIG = config.PROJECT_ROOT / "config" / "image_providers.json"
_AUTO = ("", "auto", "none", "default")


def _cfg() -> dict:
    try:
        data = json.loads(_CONFIG.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _entry(provider_id: str) -> dict | None:
    for item in _cfg().get("providers", []):
        if isinstance(item, dict) and item.get("id") == provider_id:
            return item
    return None


def _blocked(reason: str, **kw) -> dict:
    return {"ok": False, "status": opstatus.BLOCKED, "reason": reason, **kw}


def decide(project: str, provider_id: str, model_id: str,
           operation: str = "generate", quality: str = "standard",
           approval_id: str = "", confirmed_cost_usd=None) -> dict:
    """Verify selection, availability, credential source and price. No call."""
    pid = str(provider_id or "").strip()
    mid = str(model_id or "").strip()
    if pid.lower() in _AUTO or mid.lower() in _AUTO:
        return _blocked("explicit-provider-and-model-required",
                        detail="no automatic provider/model selection is "
                               "permitted under ADR-012")
    if operation not in ("generate", "inspect", "resize", "white_to_alpha",
                         "sprite_sheet"):
        return _blocked("unsupported-operation:" + operation[:80])
    provider = ip.get(pid)
    if provider is None:
        return _blocked("unknown-provider:" + pid[:80])
    entry = _entry(pid)
    if entry is None or not entry.get("enabled"):
        return _blocked("provider-not-enabled:" + pid[:80])
    declared_models = entry.get("models") or {}
    if declared_models and mid not in declared_models:
        return _blocked("model-not-in-provider-catalog:" + mid[:80])
    status = provider.status()
    if status.get("status") == ip.BLOCKED:
        return _blocked("provider-unavailable:" + pid[:80],
                        detail=status.get("reason", ""))
    local = pid == "local-pillow"
    credential_source = "none"
    env_name = str(entry.get("credential_env") or "")
    if env_name:
        # Name only — the value is NEVER read into a record, log or Git.
        if os.environ.get(env_name):
            credential_source = "environment:" + env_name
        else:
            return _blocked("credential-missing-from-environment:" + env_name)
    if local:
        est = {"ok": True, "amount_usd": 0.0, "free": True,
               "source": "local-compute"}
    else:
        est = gov.estimate(pid, mid, quality)
        if not est.get("ok"):
            return _blocked("price-not-verified:" + str(est.get("error")),
                            detail=est.get("reason", ""))
    amount = round(float(est["amount_usd"]), 4)
    free = bool(est.get("free") or amount == 0)
    paid_approval = None
    if not free:
        if not approval_id:
            return _blocked("paid-model-requires-explicit-approval",
                            estimated_cost_usd=amount,
                            cost_source=est.get("source", ""))
        try:
            confirmed = round(float(confirmed_cost_usd), 4)
        except (TypeError, ValueError):
            return _blocked("paid-cost-confirmation-required",
                            estimated_cost_usd=amount)
        if confirmed != amount:
            return _blocked("paid-cost-mismatch",
                            estimated_cost_usd=amount,
                            confirmed_cost_usd=confirmed)
        paid_approval = str(approval_id)
    payload = gov.payload_from(project, {
        "provider": pid, "model": mid, "quality": quality,
        "prompt": "", "size": "", "references": [], "operation": operation})
    return {"ok": True, "status": opstatus.PASS,
            "project": project, "provider": pid, "model": mid,
            "operation": operation, "quality": quality,
            "estimated_cost_usd": amount, "free": free,
            "cost_source": est.get("source", ""),
            "credential_source": credential_source,
            "approval_id": paid_approval,
            "request_hash": gov.request_hash(payload),
            "limits": list(status.get("limits") or []) + [
                "single call only; no retry, no provider switching, "
                "no paid fallback",
                "provider billing is authoritative; local estimate is not "
                "a spending guarantee"],
            "provider_status": status}


def consume_approval(decision: dict) -> dict:
    """One-shot consumption of a PAID approval immediately before the call."""
    if not decision.get("ok"):
        return _blocked("cannot-consume-failed-decision")
    if decision.get("free") or not decision.get("approval_id"):
        return {"ok": True, "status": opstatus.PASS,
                "note": "no payment approval needed for free/local path"}
    payload = gov.payload_from(decision["project"], {
        "provider": decision["provider"], "model": decision["model"],
        "quality": decision["quality"], "prompt": "", "size": "",
        "references": [], "operation": decision["operation"]})
    return gov.consume(decision["approval_id"], payload)


def dispatch(decision: dict, prompt: str = "", **kwargs) -> dict:
    """Call the SELECTED provider exactly once; never switch or retry."""
    gate = consume_approval(decision)
    if not gate.get("ok"):
        return gate
    provider = ip.get(decision["provider"])
    if provider is None:
        return _blocked("selected-provider-vanished")
    req = dict(kwargs)
    req["project"] = decision["project"]
    if decision["operation"] in ("generate",):
        result = provider.generate(prompt or "", **req)
    else:
        result = ip.process(decision["project"], {
            "op": decision["operation"], **req})
    out = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    out["policy"] = {"provider": decision["provider"],
                     "model": decision["model"],
                     "estimated_cost_usd": decision["estimated_cost_usd"],
                     "credential_source": decision["credential_source"],
                     "approval_id": decision.get("approval_id") or "",
                     "calls": 1, "fallback": False}
    if decision.get("approval_id"):
        gov.record_usage(decision["approval_id"], decision["provider"],
                         decision["model"])
    return out
