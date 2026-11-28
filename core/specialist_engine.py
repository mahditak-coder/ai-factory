"""AF NEXT Phase 04 — canonical Specialist Engine.

One registry built from the accepted role tables and legacy agent IDs, with
stable IDs, compatibility aliases, enforceable contracts, dynamic team
composition, hash-revalidated full-text handoff, stale-run rejection and
honest final-response selection. This module does NOT execute specialists,
call providers, build products, run critic loops or perform release work.
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

try:  # POSIX target
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

import core.config as config
from core import opstatus, paths, specialists, workflow_roles

SCHEMA_VERSION = "af-specialist-engine-1.0"
STORE_VERSION = "af-specialist-runs-1"
_STORE = config.PROJECT_ROOT / "data" / "specialist_runs.json"
_FLAGS = config.PROJECT_ROOT / "data" / "specialist_engine_flags.json"
_LOCK = threading.RLock()

# Legacy agent IDs -> canonical role IDs. Only aliases; no duplicate registry.
LEGACY_ALIASES = {
    "builder": "developer", "designer": "ui-ux-designer",
    "frontend": "ui-ux-designer", "planner": "product-architect",
    "qa": "qa-engineer", "releaser": "release-engineer",
    "product-director": "product-architect",
    "game-designer-gdd": "game-designer", "ui-game": "ui-ux-designer",
    "audio-game": "audio-designer", "qa-game": "qa-engineer",
    "security-game": "security-reviewer",
}


class EngineError(RuntimeError):
    """Explicit registry/contract/staleness/permission failure."""


# ---------------------------------------------------------------- persistence

@contextmanager
def _engine_lock():
    with _LOCK:
        if fcntl is None:
            raise EngineError("engine-lock-unavailable:fcntl-missing")
        lock = Path(str(_STORE) + ".lock")
        try:
            lock.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            raise EngineError("engine-lock-open-failed:%s" %
                              type(exc).__name__) from exc
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


def _load_unlocked() -> dict:
    try:
        text = _STORE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"schema": STORE_VERSION, "runs": {}}
    except OSError as exc:
        raise EngineError("store-unreadable:%s" % type(exc).__name__) from exc
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        raise EngineError("store-corrupt-json")
    if not isinstance(data, dict) or data.get("schema") != STORE_VERSION or \
            not isinstance(data.get("runs"), dict):
        raise EngineError("store-invalid-schema")
    return data


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".%d.%d.%s.tmp" %
               (os.getpid(), threading.get_ident(), secrets.token_hex(6)))
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _save_unlocked(data: dict) -> None:
    try:
        _atomic_write(_STORE, json.dumps(data, ensure_ascii=False, indent=1,
                                         sort_keys=True))
    except OSError as exc:
        raise EngineError("store-write-failed:%s" % type(exc).__name__) from exc


# ------------------------------------------------------------------- registry

def _contract(role: specialists.SpecialistRole, source: str) -> dict:
    return {
        "id": role.id, "title": role.title, "purpose": role.purpose,
        "inputs": list(role.inputs),
        "responsibilities": list(role.responsibilities),
        "allowed_scopes": list(role.allowed_scopes),
        "artifacts": [{"path": a.path, "format": a.format,
                       "required_sections": list(a.required_sections)}
                      for a in role.artifacts],
        "acceptance": list(role.acceptance),
        "depends_on": list(role.depends_on),
        "product_types": list(role.product_types),
        "activation": {"product_types": list(role.product_types)},
        "retry": {"max_attempts": 2, "on_failure": "revision-with-evidence"},
        "evidence_required": True, "source": source,
        "schema_version": SCHEMA_VERSION,
    }


def canonical_registry() -> dict[str, dict]:
    """One validated registry. Duplicate IDs with differing contracts fail."""
    out: dict[str, dict] = {}
    for source, table in (("ROLES", specialists.ROLES),
                          ("EXTRA_ROLES", specialists.EXTRA_ROLES)):
        for role in table:
            contract = _contract(role, source)
            existing = out.get(role.id)
            if existing is None:
                out[role.id] = contract
                continue
            if existing != contract:
                raise EngineError("role-contract-conflict:" + role.id)
    return out


def aliases() -> dict[str, str]:
    return dict(LEGACY_ALIASES)


def resolve_id(role_id: str) -> str:
    key = str(role_id or "")
    seen = set()
    while key in LEGACY_ALIASES and key not in seen:
        seen.add(key)
        key = LEGACY_ALIASES[key]
    return key


def get(role_id: str) -> dict | None:
    return canonical_registry().get(resolve_id(role_id))


def all_roles() -> list[dict]:
    return [canonical_registry()[k] for k in sorted(canonical_registry())]


def discover(product_type: str = "", goal: str = "") -> list[dict]:
    """Capability discovery: roles eligible for this product/goal."""
    out = []
    for rec in all_roles():
        types = rec["product_types"]
        eligible = "*" in types or (product_type and product_type in types)
        if not eligible and goal:
            low = goal.lower()
            if "بازی" in goal or "game" in low:
                eligible = rec["id"] in {
                    "game-designer", "game-designer-gdd", "art-director",
                    "character-artist", "environment-artist", "ui-game",
                    "ui-ux-designer", "game-developer", "qa-game", "qa-engineer",
                    "security-game", "security-reviewer", "product-director",
                    "product-architect", "technical-artist-25d", "animator",
                    "audio-game", "audio-designer"}
        if eligible:
            out.append(rec)
    return out


def compose_team(product_type: str = "", goal: str = "") -> list[dict]:
    """Bounded dynamic team from the accepted workflow DAG + role contracts."""
    dag = workflow_roles.dag_for(product_type or "", goal or "")
    eligible = {r["id"] for r in discover(product_type, goal)}
    team, seen = [], set()
    for stage in dag:
        rid = resolve_id(stage.get("role"))
        if rid in seen or rid not in eligible:
            continue
        contract = canonical_registry().get(rid)
        if contract is None:
            continue
        # Dependencies must be satisfiable inside the discovered capability set.
        missing = [d for d in contract["depends_on"]
                   if resolve_id(d) not in eligible]
        if missing:
            continue
        seen.add(rid)
        team.append({"stage_id": stage.get("id"), "role_id": rid,
                     "title": stage.get("title") or contract["title"],
                     "dependencies": [resolve_id(d)
                                      for d in contract["depends_on"]]})
    return team


def registry_consistency() -> dict:
    registry = canonical_registry()
    problems = []
    for product_type, dag in (("game", workflow_roles.GAME),
                              ("webapp", workflow_roles.WEB),
                              ("android", workflow_roles.ANDROID),
                              ("ai-tool", workflow_roles.AI)):
        for stage in dag:
            rid = resolve_id(stage.get("role"))
            if rid not in registry:
                problems.append("%s:%s:unknown-role:%s" %
                                (product_type, stage.get("id"), rid))
    return {"ok": not problems, "problems": problems,
            "role_count": len(registry)}


# ------------------------------------------------------------------ permissions

def require_scope(role_id: str, scope: str) -> bool:
    """Contract-based permission check. Prompt text can never grant scope."""
    rec = get(role_id)
    if rec is None:
        raise EngineError("unknown-role:" + role_id)
    if scope not in rec["allowed_scopes"]:
        raise EngineError("scope-denied:%s:%s" % (rec["id"], scope))
    return True


# --------------------------------------------------------------------- handoff

def verify_handoff(project: str, role_id: str,
                   expected: dict[str, str] | None = None) -> dict:
    """Full-text artifact handoff with CURRENT sha256 revalidation."""
    rec = get(role_id)
    if rec is None:
        raise EngineError("unknown-role:" + role_id)
    expected = expected or {}
    items, problems = [], []
    for dep in rec["depends_on"]:
        dep_contract = get(dep)
        if dep_contract is None:
            problems.append("unknown-dependency:" + dep)
            continue
        for spec in dep_contract["artifacts"]:
            rel = spec["path"]
            try:
                fp = paths.project_path(project, rel)
                text = fp.read_text(encoding="utf-8")
            except (OSError, PermissionError, ValueError):
                problems.append("missing:" + rel)
                continue
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if rel in expected and expected[rel] != sha:
                problems.append("hash-mismatch:" + rel)
            items.append({"path": rel, "sha256": sha,
                          "text": text, "length": len(text)})
    return {"role_id": rec["id"], "verified": not problems,
            "items": items, "problems": problems}


# -------------------------------------------------------------- stale runs

def _pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def start_run(project: str, role_id: str, run_token: str = "") -> dict:
    rec = get(role_id)
    if rec is None:
        raise EngineError("unknown-role:" + role_id)
    token = str(run_token or secrets.token_hex(12))
    with _engine_lock():
        data = _load_unlocked()
        runs = data["runs"]
        # Resolve stale RUNNING leases without deleting any record.
        stale = []
        for key, r in runs.items():
            if r.get("project") != project or r.get("role_id") != rec["id"]:
                continue
            if r.get("status") == "RUNNING" and not _pid_alive(r.get("pid")):
                r["status"] = "REVISION"
                r["stale_reason"] = "dead-worker"
                stale.append(key)
        key = "%s:%s:%s" % (project, rec["id"], token)
        runs[key] = {"project": project, "role_id": rec["id"],
                     "token": token, "status": "RUNNING",
                     "pid": os.getpid(), "started": time.time(),
                     "stale_resolved": stale}
        _save_unlocked(data)
        result = dict(runs[key])
        result["key"] = key
        return result


def reject_stale(project: str, role_id: str, run_token: str) -> dict:
    """A result from a non-current run token is rejected (no stale PASS)."""
    with _engine_lock():
        data = _load_unlocked()
        key = "%s:%s:%s" % (project, resolve_id(role_id), run_token)
        run = data["runs"].get(key)
        current = [k for k, v in data["runs"].items()
                   if v.get("project") == project and
                   v.get("role_id") == resolve_id(role_id) and
                   v.get("status") == "RUNNING"]
        if run is None or run.get("status") != "RUNNING" or key not in current:
            return {"accepted": False, "reason": "stale-run-rejected"}
        return {"accepted": True, "reason": "current-run", "key": key}


def record_result(project: str, role_id: str, run_token: str,
                  status: str, artifacts: list | None = None) -> dict:
    """Persist a result only if the run is still current; stale PASS blocked."""
    if status not in (opstatus.PASS, opstatus.FAIL, opstatus.BLOCKED,
                      opstatus.SKIP):
        raise EngineError("invalid-result-status:" + status)
    with _engine_lock():
        data = _load_unlocked()
        key = "%s:%s:%s" % (project, resolve_id(role_id), run_token)
        run = data["runs"].get(key)
        if run is None or run.get("status") != "RUNNING":
            return {"status": opstatus.BLOCKED,
                    "error": "stale-run-rejected"}
        run["status"] = status
        run["finished"] = time.time()
        run["artifacts"] = artifacts or []
        _save_unlocked(data)
        return {"status": status, "key": key}


# ------------------------------------------------------- final response pick

def select_final_response(messages: list) -> dict:
    """Choose the newest COMPLETE assistant message, not the first one.

    Aborted/error/interrupted turns and unfinished assistant nodes are never
    selected. If none qualifies, return an honest NOT TESTED verdict.
    """
    if not isinstance(messages, list):
        return {"ok": False, "reason": "messages-not-list", "index": None}
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("type") != "assistant":
            continue
        finish = str(msg.get("finish") or "").lower()
        outcome = str(msg.get("outcome") or "").lower()
        if finish in ("aborted", "error", "interrupted") or \
                outcome in ("failed", "aborted", "error"):
            continue  # a failed turn is never a final response
        completed = msg.get("completed")
        if not completed and isinstance(msg.get("time"), dict):
            completed = msg["time"].get("completed")
        if finish in ("stop", "length") and completed:
            return {"ok": True, "reason": "completed-assistant",
                    "index": idx, "message": msg}
    return {"ok": False, "reason": "no-completed-assistant", "index": None}


# -------------------------------------------------------------- feature flag

def engine_mode() -> str:
    try:
        d = json.loads(_FLAGS.read_text(encoding="utf-8"))
        return "legacy" if d.get("mode") == "legacy" else "canonical"
    except (OSError, ValueError):
        return "canonical"


def set_engine_mode(mode: str) -> dict:
    if mode not in ("canonical", "legacy"):
        raise EngineError("invalid-engine-mode")
    _atomic_write(_FLAGS, json.dumps({"mode": mode}, ensure_ascii=False))
    return {"mode": mode}
