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

Paid consumption is performed HERE, once, under a cross-process flock. The
adapter receives a single-use token so it must NOT consume the approval again.
Direct adapter use (without a token) keeps the pre-existing governance contract.
"""
from __future__ import annotations

import fcntl
import json
import os
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import core.config as config
from core import image_governance as gov
from core import image_providers as ip
from core import opstatus

_CONFIG = config.PROJECT_ROOT / "config" / "image_providers.json"
_CONSUME_LOCK = config.PROJECT_ROOT / "data" / "image_approval_consume.lock"
_AUTO = ("", "auto", "none", "default")
_TOKEN_TTL_SECONDS = 60
_tokens: dict[str, dict] = {}
_token_lock = threading.Lock()


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


@contextmanager
def _consume_lock():
    """Serialise approval consumption across threads AND processes."""
    _CONSUME_LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(_CONSUME_LOCK), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        os.close(fd)


def _payload(project: str, provider: str, model: str, quality: str,
             operation: str, prompt: str = "", size: str = "",
             references=None, region: str = "") -> dict:
    """Exact request binding — prompt/form/references are part of the hash."""
    return gov.payload_from(project, {
        "provider": provider, "model": model, "quality": quality,
        "prompt": prompt, "size": size,
        "references": list(references or []), "operation": operation,
        "region": region})


def decide(project: str, provider_id: str, model_id: str,
           operation: str = "generate", quality: str = "standard",
           approval_id: str = "", confirmed_cost_usd=None,
           prompt: str = "", size: str = "", references=None,
           region: str = "") -> dict:
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
    confirmed = None
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
        info = gov.approval_info(paid_approval)
        if not info.get("ok"):
            return _blocked("paid-approval-invalid:" +
                            str(info.get("error")))
        if info.get("expired"):
            return _blocked("paid-approval-expired",
                            age_seconds=info.get("age_seconds"))
        if info.get("project") != project or info.get("provider") != pid or \
                info.get("model") != mid:
            return _blocked("paid-approval-binding-mismatch")
        if float(info.get("estimated_cost_usd") or -1) != amount:
            return _blocked("paid-approval-cost-mismatch")
        expected_hash = gov.request_hash(_payload(
            project, pid, mid, quality, operation, prompt, size,
            references, region))
        if info.get("request_hash") != expected_hash:
            return _blocked("paid-approval-request-mismatch")
    payload = _payload(project, pid, mid, quality, operation, prompt,
                       size, references, region)
    return {"ok": True, "status": opstatus.PASS,
            "project": project, "provider": pid, "model": mid,
            "operation": operation, "quality": quality,
            "prompt": str(prompt), "size": str(size),
            "references": list(references or []), "region": str(region),
            "estimated_cost_usd": amount, "confirmed_cost_usd": confirmed,
            "free": free, "cost_source": est.get("source", ""),
            "credential_source": credential_source,
            "approval_id": paid_approval,
            "request_hash": gov.request_hash(payload),
            "limits": list(status.get("limits") or []) + [
                "single call only; no retry, no provider switching, "
                "no paid fallback",
                "provider billing is authoritative; local estimate is not "
                "a spending guarantee"],
            "provider_status": status}


def _issue_token(approval_id: str, provider: str, model: str,
                 request_hash: str, cost: float) -> str:
    token = secrets.token_urlsafe(24)
    with _token_lock:
        _tokens[token] = {"approval_id": str(approval_id or ""),
                          "request_hash": str(request_hash),
                          "provider": str(provider), "model": str(model),
                          "cost_usd": float(cost),
                          "expires_at": time.time() + _TOKEN_TTL_SECONDS}
    return token


def consume_approval(decision: dict) -> dict:
    """Atomically consume a PAID approval ONCE and issue a single-use token.

    A free/local path issues an approval-free authorization token instead: the
    model was still explicitly selected and its price verified as zero.
    """
    if not decision.get("ok"):
        return _blocked("cannot-consume-failed-decision")
    if decision.get("free") or not decision.get("approval_id"):
        if not decision.get("free"):
            return _blocked("paid-approval-required")
        token = _issue_token("", decision["provider"], decision["model"],
                             decision["request_hash"], 0.0)
        return {"ok": True, "status": opstatus.PASS,
                "consumption_token": token, "free_authorization": True,
                "note": "explicit selection + verified zero cost; "
                        "no payment approval needed"}
    payload = _payload(decision["project"], decision["provider"],
                       decision["model"], decision["quality"],
                       decision["operation"], decision.get("prompt", ""),
                       decision.get("size", ""),
                       decision.get("references", []),
                       decision.get("region", ""))
    expected_hash = gov.request_hash(payload)
    info = gov.approval_info(decision["approval_id"])
    if not info.get("ok"):
        return _blocked("paid-approval-invalid")
    if info.get("expired"):
        return _blocked("paid-approval-expired")
    if info.get("request_hash") != expected_hash:
        return _blocked("paid-approval-request-mismatch")
    with _consume_lock():
        # The flock plus governance's own consumed flag makes reuse fail closed
        # across threads AND processes. No second consume may follow.
        verdict = gov.consume(decision["approval_id"], payload)
        if verdict.get("status") != "PASS":
            return _blocked("approval-not-consumed:" +
                            str(verdict.get("error")))
        token = _issue_token(decision["approval_id"], decision["provider"],
                             decision["model"], expected_hash,
                             float(verdict.get("cost_usd") or 0))
    return {"ok": True, "status": opstatus.PASS,
            "consumption_token": token, "cost_usd": verdict.get("cost_usd"),
            "approval_id": decision["approval_id"], "consumed_once": True}


def take_consumption(token: str, approval_id: str, provider: str,
                     model: str, request_hash: str, cost_usd) -> dict:
    """Adapter-side verification of the ONE token issued by this gate.

    The token is popped atomically: a second call, replay or mismatched
    identity is rejected without touching governance again.
    """
    with _token_lock:
        rec = _tokens.pop(str(token or ""), None)
    if rec is None:
        return {"ok": False, "error": "consumption-token-invalid-or-used"}
    if time.time() > rec["expires_at"]:
        return {"ok": False, "error": "consumption-token-expired"}
    if (rec["approval_id"] != str(approval_id or "") or
            rec["provider"] != str(provider or "") or
            rec["model"] != str(model or "") or
            rec["request_hash"] != str(request_hash or "")):
        return {"ok": False, "error": "consumption-token-binding-mismatch"}
    if cost_usd is not None and float(rec["cost_usd"]) != round(float(cost_usd), 4):
        return {"ok": False, "error": "consumption-token-cost-mismatch"}
    return {"ok": True, "cost_usd": rec["cost_usd"], "verified_once": True}


def dispatch(decision: dict, prompt: str = "", **kwargs) -> dict:
    """Call the SELECTED provider exactly once with its SELECTED model."""
    gate = consume_approval(decision)
    if not gate.get("ok"):
        return gate
    provider = ip.get(decision["provider"])
    if provider is None:
        return _blocked("selected-provider-vanished")
    req = dict(kwargs)
    # Identity of the call comes ONLY from the validated decision. Caller
    # kwargs can never replace provider, model, cost or approval binding.
    req.update({"project": decision["project"],
                "provider": decision["provider"],
                "model": decision["model"],
                "operation": decision["operation"],
                "quality": decision["quality"],
                "prompt": prompt or decision.get("prompt", ""),
                "size": decision.get("size", ""),
                "references": list(decision.get("references") or []),
                "region": decision.get("region", "")})
    if decision.get("approval_id"):
        req["approval_id"] = decision["approval_id"]
        req["confirmed_cost_usd"] = str(decision["confirmed_cost_usd"])
    if gate.get("consumption_token"):
        req["consumption_token"] = gate["consumption_token"]
    if decision["operation"] in ("generate",):
        result = provider.generate(req.pop("prompt"), **req)
    else:
        result = ip.process(decision["project"], {
            "op": decision["operation"], **req})
    out = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    out["policy"] = {"provider": decision["provider"],
                     "model": decision["model"],
                     "estimated_cost_usd": decision["estimated_cost_usd"],
                     "credential_source": decision["credential_source"],
                     "approval_id": decision.get("approval_id") or "",
                     "approval_consumed_once": bool(gate.get("consumed_once")),
                     "calls": 1, "fallback": False}
    return out
