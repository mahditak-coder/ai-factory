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

SECURITY CONTRACT (forgery fix): a decision returned by ``decide()`` is an
OPAQUE HANDLE, not an authorization object. ``ok``, ``free``, cost, provider,
model and hashes inside it are informational only. Before any token is issued,
``consume_approval()`` looks up a server-side immutable snapshot, detects any
mutation of the caller's handle, and REVALIDATES the whole request against the
CURRENT provider configuration and governance records. All values used for the
call come from that fresh verification, never from the caller's dictionary.
"""
from __future__ import annotations

import fcntl
import hashlib
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
_decisions: dict[str, dict] = {}
_token_lock = threading.Lock()
_decision_lock = threading.Lock()
_MAX_DECISIONS = 512

_PUBLIC_KEYS = (
    "project", "provider", "model", "operation", "quality", "prompt",
    "size", "references", "region", "estimated_cost_usd",
    "confirmed_cost_usd", "free", "cost_source", "credential_source",
    "approval_id", "request_hash", "limits", "provider_status")
_REQUEST_KEYS = ("project", "provider_id", "model_id", "operation", "quality",
                 "approval_id", "confirmed_cost_usd", "prompt", "size",
                 "references", "region")


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


def _validate_request(req: dict) -> tuple[dict | None, dict | None]:
    """Derive authorization ONLY from current configuration and governance.

    No field from a previously returned decision participates here. ``req`` is
    the server-owned snapshot of the ORIGINAL user selection.
    """
    pid = str(req.get("provider_id") or "").strip()
    mid = str(req.get("model_id") or "").strip()
    if pid.lower() in _AUTO or mid.lower() in _AUTO:
        return None, _blocked("explicit-provider-and-model-required",
                              detail="no automatic provider/model selection "
                                     "is permitted under ADR-012")
    operation = str(req.get("operation") or "generate")
    quality = str(req.get("quality") or "standard")
    project = str(req.get("project") or "")
    if operation not in ("generate", "inspect", "resize", "white_to_alpha",
                         "sprite_sheet"):
        return None, _blocked("unsupported-operation:" + operation[:80])
    provider = ip.get(pid)
    if provider is None:
        return None, _blocked("unknown-provider:" + pid[:80])
    entry = _entry(pid)
    if entry is None or not entry.get("enabled"):
        return None, _blocked("provider-not-enabled:" + pid[:80])
    declared_models = entry.get("models") or {}
    if declared_models and mid not in declared_models:
        return None, _blocked("model-not-in-provider-catalog:" + mid[:80])
    status = provider.status()
    if status.get("status") == ip.BLOCKED:
        return None, _blocked("provider-unavailable:" + pid[:80],
                              detail=status.get("reason", ""))
    credential_source = "none"
    env_name = str(entry.get("credential_env") or "")
    if env_name:
        # Name only — the value is NEVER read into a record, log or Git.
        if os.environ.get(env_name):
            credential_source = "environment:" + env_name
        else:
            return None, _blocked("credential-missing-from-environment:" +
                                  env_name)
    prompt = str(req.get("prompt") or "")
    size = str(req.get("size") or "")
    references = list(req.get("references") or [])
    region = str(req.get("region") or "")
    if pid == "local-pillow":
        est = {"ok": True, "amount_usd": 0.0, "free": True,
               "source": "local-compute"}
    else:
        est = gov.estimate(pid, mid, quality)
        if not est.get("ok"):
            return None, _blocked("price-not-verified:" +
                                  str(est.get("error")),
                                  detail=est.get("reason", ""))
    amount = round(float(est["amount_usd"]), 4)
    # Free/paid is determined EXCLUSIVELY by verified current pricing config.
    free = bool(est.get("free")) and amount == 0
    if not free and amount == 0:
        return None, _blocked("zero-cost-without-free-price-configuration")
    confirmed = None
    approval = None
    if not free:
        approval = str(req.get("approval_id") or "")
        if not approval:
            return None, _blocked("paid-model-requires-explicit-approval",
                                  estimated_cost_usd=amount,
                                  cost_source=est.get("source", ""))
        try:
            confirmed = round(float(req.get("confirmed_cost_usd")), 4)
        except (TypeError, ValueError):
            return None, _blocked("paid-cost-confirmation-required",
                                  estimated_cost_usd=amount)
        if confirmed != amount:
            return None, _blocked("paid-cost-mismatch",
                                  estimated_cost_usd=amount,
                                  confirmed_cost_usd=confirmed)
        info = gov.approval_info(approval)
        if not info.get("ok"):
            return None, _blocked("paid-approval-invalid:" +
                                  str(info.get("error")))
        if info.get("expired"):
            return None, _blocked("paid-approval-expired",
                                  age_seconds=info.get("age_seconds"))
        if (info.get("project") != project or info.get("provider") != pid or
                info.get("model") != mid):
            return None, _blocked("paid-approval-binding-mismatch")
        if float(info.get("estimated_cost_usd") or -1) != amount:
            return None, _blocked("paid-approval-cost-mismatch")
        if info.get("request_hash") != gov.request_hash(_payload(
                project, pid, mid, quality, operation, prompt, size,
                references, region)):
            return None, _blocked("paid-approval-request-mismatch")
    verified = {
        "project": project, "provider": pid, "model": mid,
        "operation": operation, "quality": quality, "prompt": prompt,
        "size": size, "references": references, "region": region,
        "estimated_cost_usd": amount, "confirmed_cost_usd": confirmed,
        "free": free, "cost_source": est.get("source", ""),
        "credential_source": credential_source, "approval_id": approval,
        "request_hash": gov.request_hash(_payload(
            project, pid, mid, quality, operation, prompt, size,
            references, region)),
        "limits": list(status.get("limits") or []) + [
            "single call only; no retry, no provider switching, "
            "no paid fallback",
            "provider billing is authoritative; local estimate is not "
            "a spending guarantee"],
        "provider_status": status}
    return verified, None


def _fingerprint(decision: dict) -> str:
    blob = json.dumps({k: decision.get(k) for k in _PUBLIC_KEYS},
                      ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def decide(project: str, provider_id: str, model_id: str,
           operation: str = "generate", quality: str = "standard",
           approval_id: str = "", confirmed_cost_usd=None,
           prompt: str = "", size: str = "", references=None,
           region: str = "") -> dict:
    """Validate a selection and return an OPAQUE decision handle. No call."""
    request = {"project": project, "provider_id": provider_id,
               "model_id": model_id, "operation": operation,
               "quality": quality, "approval_id": approval_id,
               "confirmed_cost_usd": confirmed_cost_usd, "prompt": prompt,
               "size": size, "references": list(references or []),
               "region": region}
    verified, problem = _validate_request(request)
    if problem is not None:
        return problem
    decision = {"ok": True, "status": opstatus.PASS, **verified}
    decision_id = "dec-" + secrets.token_urlsafe(18)
    decision["decision_id"] = decision_id
    with _decision_lock:
        if len(_decisions) >= _MAX_DECISIONS:
            oldest = next(iter(_decisions))
            _decisions.pop(oldest, None)
        _decisions[decision_id] = {
            "request": {k: (list(v) if k == "references" and isinstance(v, list)
                            else v) for k, v in request.items()},
            "fingerprint": _fingerprint(decision)}
    return decision


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
    """Revalidate the server snapshot and issue ONE authorization token.

    No authorization field in the caller's dictionary is trusted: the handle
    is only a lookup key, and every value below is freshly derived from the
    current provider configuration and governance records.
    """
    if not isinstance(decision, dict):
        return _blocked("decision-not-a-dict")
    did = str(decision.get("decision_id") or "")
    with _decision_lock:
        snapshot = _decisions.get(did)
    if snapshot is None:
        return _blocked("decision-not-registered")
    if _fingerprint(decision) != snapshot["fingerprint"]:
        return _blocked("decision-mutated-after-validation")
    verified, problem = _validate_request(snapshot["request"])
    if problem is not None:
        return problem  # config/price/approval changed since decide()
    verified = dict(verified)
    payload = _payload(verified["project"], verified["provider"],
                       verified["model"], verified["quality"],
                       verified["operation"], verified["prompt"],
                       verified["size"], verified["references"],
                       verified["region"])
    expected_hash = gov.request_hash(payload)
    if verified["free"]:
        token = _issue_token("", verified["provider"], verified["model"],
                             expected_hash, 0.0)
        return {"ok": True, "status": opstatus.PASS,
                "consumption_token": token, "free_authorization": True,
                "verified": verified,
                "note": "explicit selection + verified zero cost; "
                        "no payment approval needed"}
    with _consume_lock():
        # The flock plus governance's consumed flag makes reuse fail closed
        # across threads AND processes. Governance is touched exactly once.
        verdict = gov.consume(verified["approval_id"], payload)
        if verdict.get("status") != "PASS":
            return _blocked("approval-not-consumed:" +
                            str(verdict.get("error")))
        token = _issue_token(verified["approval_id"], verified["provider"],
                             verified["model"], expected_hash,
                             float(verdict.get("cost_usd") or 0))
    return {"ok": True, "status": opstatus.PASS,
            "consumption_token": token, "cost_usd": verdict.get("cost_usd"),
            "approval_id": verified["approval_id"], "consumed_once": True,
            "verified": verified}


def take_consumption(token: str, approval_id: str, provider: str,
                     model: str, request_hash: str, cost_usd) -> dict:
    """Adapter-side verification of the ONE token issued by this gate."""
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
    """Call the SELECTED provider once using only freshly verified identity."""
    gate = consume_approval(decision)
    if not gate.get("ok"):
        return gate
    v = gate["verified"]          # server-derived, never caller-derived
    if prompt and str(prompt) != v["prompt"]:
        return _blocked("call-prompt-changed-after-validation")
    for key in ("size", "region"):
        if key in kwargs and str(kwargs[key]) != v[key]:
            return _blocked("call-" + key + "-changed-after-validation")
    if "references" in kwargs and list(kwargs["references"]) != v["references"]:
        return _blocked("call-references-changed-after-validation")
    provider = ip.get(v["provider"])
    if provider is None:
        return _blocked("selected-provider-vanished")
    req = dict(kwargs)
    req.update({"project": v["project"], "provider": v["provider"],
                "model": v["model"], "operation": v["operation"],
                "quality": v["quality"], "prompt": v["prompt"],
                "size": v["size"], "references": list(v["references"]),
                "region": v["region"]})
    if v["approval_id"]:
        req["approval_id"] = v["approval_id"]
        req["confirmed_cost_usd"] = str(v["confirmed_cost_usd"])
    req["consumption_token"] = gate["consumption_token"]
    if v["operation"] in ("generate",):
        result = provider.generate(req.pop("prompt"), **req)
    else:
        result = ip.process(v["project"], {"op": v["operation"], **req})
    out = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    out["policy"] = {"provider": v["provider"], "model": v["model"],
                     "estimated_cost_usd": v["estimated_cost_usd"],
                     "credential_source": v["credential_source"],
                     "approval_id": v["approval_id"] or "",
                     "approval_consumed_once": bool(gate.get("consumed_once")),
                     "calls": 1, "fallback": False}
    return out
