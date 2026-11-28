"""AF NEXT Phase 03 — Research, Innovation and Product Strategy.

Turns a Phase 02 structured request into an evidence-backed, versioned Product
Brief with traceable sources, compared alternatives, honest feasibility and
testable acceptance criteria. This phase does NOT execute specialists, build
products, run providers, or approve its own final scope.

All persisted records use flock-guarded atomic writes and an explicit marker;
untrusted source text is stored as DATA, never interpreted as instructions.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path

try:  # POSIX target; non-POSIX fails closed
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

import core.config as config

SCHEMA_VERSION = "af-product-strategy-1.0"
STORE_VERSION = "af-product-store-1"
CLAIM_TYPES = ("verified_fact", "hypothesis", "estimate", "unknown")
FEASIBILITY_DIMENSIONS = ("technical", "operational", "economic", "practical")
BRIEF_STATUSES = ("DRAFT", "PENDING_USER_APPROVAL", "USER_APPROVED", "SUPERSEDED")

_STORE = config.PROJECT_ROOT / "data" / "product_strategy.json"
_LOCK = threading.RLock()


class ResearchError(RuntimeError):
    """Explicit failure: insufficient evidence, missing approval, bad schema."""


# ---------------------------------------------------------------- persistence

def _unique_tmp(path: Path) -> Path:
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
def _store_lock():
    with _LOCK:
        if fcntl is None:
            raise ResearchError("store-lock-unavailable:fcntl-missing")
        lock_path = Path(str(_STORE) + ".lock")
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            raise ResearchError("store-lock-open-failed:%s" %
                                type(exc).__name__) from exc
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError as exc:
                raise ResearchError("store-lock-acquire-failed:%s" %
                                    type(exc).__name__) from exc
            try:
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            os.close(fd)


def _empty() -> dict:
    return {"schema": STORE_VERSION,
            "sources": {}, "alternatives": {}, "feasibility": {},
            "briefs": {}, "brief_history": {}}


def _load_unlocked() -> dict:
    try:
        text = _STORE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty()
    except OSError as exc:
        raise ResearchError("store-unreadable:%s" % type(exc).__name__) from exc
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        raise ResearchError("store-corrupt-json")
    if not isinstance(data, dict) or data.get("schema") != STORE_VERSION:
        raise ResearchError("store-invalid-schema")
    for key in ("sources", "alternatives", "feasibility", "briefs",
                "brief_history"):
        if not isinstance(data.get(key), dict):
            raise ResearchError("store-invalid-section:" + key)
    return data


def _save_unlocked(data: dict) -> None:
    try:
        _atomic_write(_STORE, json.dumps(data, ensure_ascii=False, indent=1,
                                         sort_keys=True))
    except OSError as exc:
        raise ResearchError("store-write-failed:%s" % type(exc).__name__) from exc


def _mutate(fn):
    with _store_lock():
        data = _load_unlocked()
        result = fn(data)
        _save_unlocked(data)
        return result


def canonical_hash(payload) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ------------------------------------------------------------------ research

def add_source(project: str, url: str, title: str, claim: str,
               claim_type: str = "hypothesis", topic: str = "",
               evidence: str = "", accessed_at: str = "",
               researcher: str = "system:research",
               confidence=None) -> dict:
    """Record a traceable research finding. Untrusted text stays DATA."""
    project, url, claim = str(project or ""), str(url or "").strip(), str(claim or "").strip()
    if not project or not url or not claim:
        raise ResearchError("source-requires-project-url-claim")
    if claim_type not in CLAIM_TYPES:
        raise ResearchError("invalid-claim-type:" + claim_type)
    sid = "src-" + canonical_hash({"project": project, "url": url,
                                   "claim": claim})[:16]

    def fn(data):
        sources = data["sources"]
        if sid in sources:
            return dict(sources[sid])  # idempotent
        rec = {"source_id": sid, "project": project, "url": url[:2000],
               "title": str(title or "")[:500], "claim": claim[:2000],
               "claim_type": claim_type, "topic": str(topic or "")[:200],
               "evidence": str(evidence or "")[:2000],
               "accessed_at": str(accessed_at or _now()),
               "created": _now(),
               "confidence": confidence,
               "provenance": {"researcher": str(researcher or "system:research"),
                              "method": "declared-source",
                              "at": _now(),
                              "hash": canonical_hash({"url": url, "claim": claim})}}
        sources[sid] = rec
        return rec

    return _mutate(fn)


def sources(project: str) -> list[dict]:
    with _store_lock():
        items = _load_unlocked()["sources"]
    return [dict(v) for v in items.values() if v.get("project") == project]


def source(source_id: str) -> dict | None:
    with _store_lock():
        rec = _load_unlocked()["sources"].get(str(source_id or ""))
    return dict(rec) if rec else None


def assess_sources(project: str) -> dict:
    """Detect insufficient, unverified or contradictory evidence."""
    items = sources(project)
    verified = [s for s in items if s["claim_type"] == "verified_fact"]
    unverified = [s for s in items if s["claim_type"] in ("hypothesis",
                                                           "estimate", "unknown")]
    conflicts = []
    for i, a in enumerate(verified):
        for b in verified[i + 1:]:
            if a.get("topic") and a["topic"] == b.get("topic") and \
                    a["claim"] != b["claim"]:
                conflicts.append({"a": a["source_id"], "b": b["source_id"],
                                  "topic": a["topic"], "material": True})
    return {"sufficient": bool(verified) and not conflicts,
            "verified_count": len(verified), "unverified": unverified,
            "conflicts": conflicts,
            "reasons": (["no-verified-source"] if not verified else []) +
                       (["contradictory-sources"] if conflicts else [])}


# --------------------------------- paid research (never auto-executed)

def propose_paid_research(project: str, description: str) -> dict:
    """Create an explicit approval token for a PAID research action.

    No provider is contacted here. This only records the request.
    """
    from core import confirmations
    description = str(description or "").strip()
    if not project or not description:
        raise ResearchError("paid-research-description-required")
    cf = confirmations.create(str(project), "phase03-paid-research",
                              {"project": str(project),
                               "description": description[:1000]},
                              "پژوهش پولی نیاز به تأیید صریح دارد")
    return {"confirm_token": cf["confirm_token"], "project": project,
            "description": description[:1000], "question": cf["question"]}


def run_paid_research(project: str, token: str = "", actor: str = "") -> dict:
    """Fail closed: no paid provider/adapter exists in Phase 03.

    Even with explicit approval, no paid side effect is executed; the honest
    result is BLOCKED, not a fabricated success.
    """
    if not str(token or ""):
        raise ResearchError("paid-research-approval-required")
    from core import confirmations
    try:
        confirmations.peek(str(token), str(project))
    except confirmations.ConfirmError as exc:
        raise ResearchError("paid-research-approval-invalid:" + exc.message) from exc
    return {"status": "BLOCKED", "paid_executed": False,
            "reason": "paid-provider-not-available-in-phase03"}


# --------------------------------------------------------- alternatives

def add_alternative(project: str, option_id: str, name: str,
                    description: str, criteria: dict | None = None,
                    pros: list | None = None, cons: list | None = None,
                    limits: list | None = None,
                    evidence_source_ids: list | None = None) -> dict:
    option_id = str(option_id or "").strip()
    if not project or not option_id or not str(name or "").strip():
        raise ResearchError("alternative-requires-id-name")
    valid = ("cost", "complexity", "risk", "time", "feasibility")
    scores = {}
    for key, value in (criteria or {}).items():
        if key not in valid:
            raise ResearchError("invalid-criterion:" + key)
        try:
            scores[key] = max(0, min(5, int(value)))
        except (TypeError, ValueError):
            scores[key] = None  # explicitly unknown, never fabricated
    rec = {"option_id": option_id, "project": project, "name": str(name)[:300],
           "description": str(description or "")[:2000],
           "criteria": scores, "pros": list(pros or []), "cons": list(cons or []),
           "limits": list(limits or []),
           "evidence_source_ids": list(evidence_source_ids or []),
           "status": "considered", "reject_reason": "", "at": _now()}

    def fn(data):
        data["alternatives"][option_id] = rec
        return rec

    return _mutate(fn)


def reject_alternative(option_id: str, reason: str) -> dict:
    reason = str(reason or "").strip()
    if not reason:
        raise ResearchError("reject-reason-required")

    def fn(data):
        rec = data["alternatives"].get(str(option_id or ""))
        if rec is None:
            raise ResearchError("alternative-not-found:" + option_id)
        rec["status"] = "rejected"
        rec["reject_reason"] = reason[:600]
        return rec

    return _mutate(fn)


def compare_alternatives(project: str, auto_select: bool = False) -> dict:
    """Transparent comparison; auto-selection blocked without solid evidence."""
    with _store_lock():
        items = [dict(v) for v in
                 _load_unlocked()["alternatives"].values()
                 if v.get("project") == project]
    ids = {s["source_id"] for s in sources(project)
           if s["claim_type"] == "verified_fact"}
    rows = []
    for alt in items:
        known = {k: v for k, v in alt["criteria"].items() if v is not None}
        scores = {"cost": known.get("cost"), "complexity": known.get("complexity"),
                  "risk": known.get("risk"), "time": known.get("time"),
                  "feasibility": known.get("feasibility")}
        evidence_ok = bool(alt["evidence_source_ids"]) and \
            all(x in ids for x in alt["evidence_source_ids"])
        total = sum(scores.values()) if len(scores) == 5 and \
            all(v is not None for v in scores.values()) else None
        rows.append({"option_id": alt["option_id"], "name": alt["name"],
                     "status": alt["status"], "reject_reason": alt["reject_reason"],
                     "scores": scores, "score_total": total,
                     "evidence_ok": evidence_ok,
                     "evidence_source_ids": alt["evidence_source_ids"]})
    selectable = [r for r in rows if r["status"] == "considered" and
                  r["evidence_ok"] and r["score_total"] is not None]
    selected = None
    blocked = "insufficient-evidence"
    if auto_select:
        if not selectable:
            blocked = "auto-selection-blocked:insufficient-evidence"
        elif all(r["score_total"] == selectable[0]["score_total"]
                 for r in selectable):
            blocked = "auto-selection-blocked:tie"
        else:
            selected = max(selectable, key=lambda r: r["score_total"])["option_id"]
            blocked = ""
    return {"project": project, "options": rows, "selected": selected,
            "auto_selection_blocked": bool(blocked), "blocked_reason": blocked}


# ------------------------------------------------------------- feasibility

def assess_feasibility(project: str, dimensions: dict | None = None,
                       dependencies: list | None = None,
                       risks: list | None = None,
                       environment_limits: list | None = None) -> dict:
    dims = {}
    for name in FEASIBILITY_DIMENSIONS:
        value = (dimensions or {}).get(name) or {}
        if isinstance(value, str):
            value = {"status": "unknown", "notes": value}
        status = str(value.get("status") or "unknown")
        if status not in ("verified", "unknown", "blocked"):
            raise ResearchError("invalid-feasibility-status:" + status)
        dims[name] = {"status": status,
                      "notes": str(value.get("notes") or "")[:1000],
                      "evidence": list(value.get("evidence") or [])}
    rec = {"project": project, "dimensions": dims,
           "dependencies": list(dependencies or []),
           "risks": list(risks or []),
           "environment_limits": list(environment_limits or []),
           "requires_human_review": any(
               d["status"] != "verified" for d in dims.values()),
           "at": _now()}

    def fn(data):
        data["feasibility"][project] = rec
        return rec

    return _mutate(fn)


# ----------------------------------------------- acceptance criteria & brief

def validate_acceptance_criteria(criteria: list) -> dict:
    errors, unknowns = [], []
    if not isinstance(criteria, list) or not criteria:
        return {"valid": False, "errors": ["criteria-required"],
                "unknowns": unknowns}
    ids = set()
    for i, c in enumerate(criteria):
        if not isinstance(c, dict):
            errors.append("criteria-not-dict:%d" % i)
            continue
        cid = str(c.get("id") or "")
        if not cid or cid in ids:
            errors.append("criteria-id-invalid:%d" % i)
        ids.add(cid)
        if not str(c.get("description") or "").strip():
            errors.append("criteria-description-required:" + cid)
        if c.get("measurable") is not True:
            unknowns.append(cid + ":measurable")
        if c.get("testable") is not True:
            unknowns.append(cid + ":testable")
        if not c.get("linked_source") and not c.get("linked_assumption"):
            unknowns.append(cid + ":link")
        if str(c.get("status") or "unknown") == "unknown":
            unknowns.append(cid + ":status")
    return {"valid": not errors, "errors": errors, "unknowns": unknowns}


_BRIEF_SECTIONS = ("problem", "goal", "target_users", "user_needs",
                   "definition", "value_proposition", "scope", "out_of_scope",
                   "prioritized_features", "assumptions", "risks",
                   "dependencies", "sources", "alternatives", "feasibility",
                   "acceptance_criteria", "success_metrics")


def create_brief(project: str, request_id: str = "", sections: dict | None = None,
                 source_ids: list | None = None, alternative_ids: list | None = None,
                 feasibility_project: str = "") -> dict:
    if not project:
        raise ResearchError("brief-requires-project")
    secs = {k: (sections or {}).get(k) for k in _BRIEF_SECTIONS}
    criteria_check = validate_acceptance_criteria(secs["acceptance_criteria"] or [])
    if not criteria_check["valid"]:
        raise ResearchError("acceptance-criteria-invalid:" +
                            ",".join(criteria_check["errors"][:3]))
    source_ids = list(source_ids or [])
    known = {s["source_id"] for s in sources(project)}
    bad = [x for x in source_ids if x not in known]
    if bad:
        raise ResearchError("brief-source-not-found:" + bad[0])

    def fn(data):
        prefix = "brief-" + project
        version = 1 + sum(1 for k in data["briefs"]
                          if k.startswith(prefix))
        bid = "%s-v%d" % (prefix, version)
        body = {"brief_id": bid, "project": project, "request_id": str(request_id),
                "schema_version": SCHEMA_VERSION, "version": version,
                "status": "PENDING_USER_APPROVAL", "created": _now(),
                "updated": _now(), "sections": secs,
                "source_ids": source_ids, "alternative_ids": list(alternative_ids or []),
                "feasibility": data["feasibility"].get(feasibility_project or project, {}),
                "criteria_validation": criteria_check,
                "user_approval": {"status": "NOT_APPROVED", "actor": "",
                                  "token": "", "at": ""},
                "history": []}
        body["hash"] = canonical_hash({k: v for k, v in body.items()
                                       if k != "hash"})
        data["briefs"][bid] = body
        return dict(body)

    return _mutate(fn)


def revise_brief(brief_id: str, sections: dict | None = None,
                 reason: str = "") -> dict:
    if not str(reason or "").strip():
        raise ResearchError("revision-reason-required")

    def fn(data):
        old = data["briefs"].get(str(brief_id or ""))
        if old is None:
            raise ResearchError("brief-not-found:" + brief_id)
        if old.get("status") == "USER_APPROVED":
            raise ResearchError("approved-brief-is-readonly")
        snapshot = json.loads(json.dumps(old, ensure_ascii=False))
        snapshot["status"] = "SUPERSEDED"
        key = "%s-history" % brief_id
        data["brief_history"].setdefault(key, []).append(snapshot)
        new = dict(old)
        merged = dict(old.get("sections") or {})
        for k, v in (sections or {}).items():
            if k in _BRIEF_SECTIONS:
                merged[k] = v
        check = validate_acceptance_criteria(merged.get("acceptance_criteria") or [])
        if not check["valid"]:
            raise ResearchError("acceptance-criteria-invalid")
        new["sections"] = merged
        new["version"] = int(old["version"]) + 1
        new["brief_id"] = "%s-v%d" % (old["brief_id"].rsplit("-v", 1)[0],
                                      new["version"])
        new["status"] = "PENDING_USER_APPROVAL"
        new["criteria_validation"] = check
        new["updated"] = _now()
        new["revision_reason"] = str(reason)[:500]
        new["history"] = list(old.get("history") or []) + [{
            "brief_id": old["brief_id"], "version": old["version"],
            "hash": old.get("hash"), "status": "SUPERSEDED",
            "reason": str(reason)[:400], "at": _now()}]
        new["user_approval"] = {"status": "NOT_APPROVED", "actor": "",
                                "token": "", "at": ""}
        new["hash"] = canonical_hash({k: v for k, v in new.items()
                                      if k not in ("hash",)})
        # Keep the previous version readable and immutable as history.
        old["status"] = "SUPERSEDED"
        data["briefs"][brief_id] = old
        data["briefs"][new["brief_id"]] = new
        return dict(new)

    return _mutate(fn)


def brief(brief_id: str) -> dict | None:
    with _store_lock():
        rec = _load_unlocked()["briefs"].get(str(brief_id or ""))
    return dict(rec) if rec else None


def brief_history(project: str) -> list[dict]:
    with _store_lock():
        data = _load_unlocked()
    out = []
    for key, versions in data["brief_history"].items():
        for rec in versions:
            if rec.get("project") == project:
                out.append(dict(rec))
    return sorted(out, key=lambda r: (r.get("brief_id", ""), r.get("version", 0)))


def write_brief_artifact(brief_id: str) -> dict:
    """Persist a real versioned artifact file with its SHA-256."""
    rec = brief(brief_id)
    if rec is None:
        raise ResearchError("brief-not-found:" + brief_id)
    project = rec["project"]
    d = config.PROJECTS_DIR / project / "docs" / "product"
    d.mkdir(parents=True, exist_ok=True)
    path = d / ("brief-v%d.json" % rec["version"])
    text = json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True)
    _atomic_write(path, text)
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {"path": str(path.relative_to(config.PROJECT_ROOT)),
            "sha256": sha, "brief_id": brief_id, "version": rec["version"]}


def approve_brief(brief_id: str, token: str, actor: str) -> dict:
    """Explicit user acceptance of final scope/success criteria.

    Without a genuine confirmation token this never marks a brief final.
    """
    from core import confirmations
    token = str(token or "")
    if not token or not str(actor or "").strip():
        raise ResearchError("brief-approval-requires-token-and-actor")
    rec = brief(brief_id)
    if rec is None:
        raise ResearchError("brief-not-found:" + brief_id)
    try:
        v = confirmations.peek(token, rec["project"])
    except confirmations.ConfirmError as exc:
        raise ResearchError("brief-approval-invalid:" + exc.message) from exc
    payload = v.get("payload") or {}
    if payload.get("brief_id") != brief_id or \
            payload.get("hash") != rec.get("hash"):
        raise ResearchError("brief-approval-binding-mismatch")

    def fn(data):
        cur = data["briefs"].get(brief_id)
        if cur is None:
            raise ResearchError("brief-not-found:" + brief_id)
        confirmations.consume(token, rec["project"])
        cur["user_approval"] = {"status": "APPROVED", "actor": str(actor),
                                "at": _now(), "token_consumed": True}
        cur["status"] = "USER_APPROVED"
        cur["updated"] = _now()
        return dict(cur)

    return _mutate(fn)


def propose_brief_approval(brief_id: str) -> dict:
    from core import confirmations
    rec = brief(brief_id)
    if rec is None:
        raise ResearchError("brief-not-found:" + brief_id)
    if rec["status"] == "USER_APPROVED":
        raise ResearchError("brief-already-approved")
    if rec["status"] == "SUPERSEDED":
        raise ResearchError("brief-superseded")
    cf = confirmations.create(
        rec["project"], "phase03-product-brief-approval",
        {"brief_id": brief_id, "hash": rec.get("hash")},
        "تأیید نهایی محدوده و معیارهای موفقیت محصول")
    return {"confirm_token": cf["confirm_token"], "brief_id": brief_id,
            "hash": rec.get("hash"), "question": cf["question"]}
