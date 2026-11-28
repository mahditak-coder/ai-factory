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
# Phase 04 audit repair: the canonical engine MUST NOT share the legacy
# list-shaped agent_collaboration store (data/specialist_runs.json).
_STORE = config.PROJECT_ROOT / "data" / "canonical_specialist_runs.json"
_LEGACY_STORE = config.PROJECT_ROOT / "data" / "specialist_runs.json"
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
    # Phase 05 domain capability aliases (old/conversational names).
    "backend": "backend-specialist", "back-end": "backend-specialist",
    "front-end": "frontend-specialist",
    "database": "database-specialist", "db": "database-specialist",
    "api": "api-specialist", "devops": "devops-specialist",
    "mobile-ux": "mobile-ux-specialist",
    "app-architecture": "application-architect",
    "level": "level-designer", "balance": "game-balance-specialist",
    "narrative": "narrative-designer", "education": "education-specialist",
    "curriculum": "curriculum-designer",
    "learning-experience": "learning-experience-specialist",
    "accessibility": "accessibility-specialist",
    "visual": "visual-designer", "music": "music-sound-specialist",
}


# Intentional legacy aliases that are themselves canonical role IDs (kept so
# historical workflow DAG IDs still resolve). Any other alias key that is a
# canonical role ID is an unintended shadowing collision.
_INTENTIONAL_CANONICAL_ALIASES = frozenset((
    "product-director", "game-designer-gdd", "ui-game", "audio-game",
    "qa-game", "security-game",
))


def alias_report() -> dict:
    """Detect duplicate keys, alias cycles and unexpected canonical shadowing."""
    registry = canonical_registry()
    problems = []
    for key, target in LEGACY_ALIASES.items():
        seen, node = set(), key
        while node in LEGACY_ALIASES:
            if node in seen:
                problems.append("alias-cycle:" + key)
                break
            seen.add(node)
            node = LEGACY_ALIASES[node]
        if node not in registry:
            problems.append("alias-unresolved:" + key)
        if key in registry and key not in _INTENTIONAL_CANONICAL_ALIASES:
            problems.append("alias-shadows-canonical:" + key)
    return {"ok": not problems, "problems": problems,
            "aliases": dict(LEGACY_ALIASES)}


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


def migrate_legacy_runs() -> dict:
    """Explicit, non-destructive migration of legacy list-shaped run records.

    Legacy data is never deleted or rewritten; canonical records are inserted
    once (idempotent). No automatic migration happens on ordinary reads.
    """
    with _engine_lock():
        legacy = []
        try:
            raw = json.loads(_LEGACY_STORE.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                legacy = raw
        except (OSError, ValueError):
            legacy = []
        data = _load_unlocked()
        runs = data["runs"]
        migrated, skipped = 0, 0
        for idx, item in enumerate(legacy):
            if not isinstance(item, dict):
                skipped += 1
                continue
            ref = str(item.get("id") or item.get("call_id") or
                      "legacy-%d" % idx)
            key = "legacy:" + ref
            if key in runs:
                skipped += 1
                continue
            runs[key] = {"project": str(item.get("project") or ""),
                         "role_id": resolve_id(str(item.get("role") or "")),
                         "token": "legacy-" + ref, "status": str(
                             item.get("status") or "unknown"),
                         "legacy_ref": ref,
                         "legacy": dict(item),
                         "migrated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            migrated += 1
        if migrated:
            _save_unlocked(data)
        return {"migrated": migrated, "skipped": skipped,
                "total": len(runs)}


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
                          ("EXTRA_ROLES", specialists.EXTRA_ROLES),
                          ("DOMAIN_ROLES", specialists.DOMAIN_ROLES)):
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


def has_accepted_run(project: str, role_id: str) -> bool:
    rid = resolve_id(role_id)
    with _engine_lock():
        data = _load_unlocked()
    return any(r.get("project") == project and r.get("role_id") == rid and
               r.get("status") == opstatus.PASS
               for r in data["runs"].values())


def accepted_producer_hashes(project: str, producer_role: str) -> dict:
    """Artifact hashes from the authoritative accepted producer run record.

    Fail-closed: a missing, non-PASS or unhashed producer yields no evidence.
    """
    rid = resolve_id(producer_role)
    with _engine_lock():
        data = _load_unlocked()
        rows = [r for r in data["runs"].values()
                if r.get("project") == project and r.get("role_id") == rid and
                r.get("status") == opstatus.PASS]
    if not rows:
        return {}
    accepted = max(rows, key=lambda r: float(r.get("finished") or
                                             r.get("started") or 0))
    out = {}
    for art in accepted.get("artifacts") or []:
        if isinstance(art, dict) and art.get("path") and art.get("sha256"):
            out[str(art["path"])] = str(art["sha256"])
    return out


def catalog_reconciliation() -> dict:
    """Reconcile every legacy role source against the canonical registry."""
    registry = canonical_registry()
    sources: dict[str, list[str]] = {"ROLES": [r.id for r in specialists.ROLES],
                                     "EXTRA_ROLES": [r.id for r in
                                                     specialists.EXTRA_ROLES],
                                     "DOMAIN_ROLES": [r.id for r in
                                                      specialists.DOMAIN_ROLES]}
    agents_dir = config.AGENTS_DIR
    agent_names = []
    try:
        for fp in sorted(agents_dir.glob("*.json")):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("name"):
                    agent_names.append(str(data["name"]))
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    sources["agents/*.json"] = agent_names
    dag_names = [stage.get("role") for dag in
                 (workflow_roles.GAME, workflow_roles.WEB,
                  workflow_roles.ANDROID, workflow_roles.AI)
                 for stage in dag if stage.get("role")]
    sources["workflow DAGs"] = sorted(set(dag_names))
    rows, problems = [], []
    expected_domain = {r.id for r in specialists.DOMAIN_ROLES}
    present_domain = expected_domain & set(registry)
    if present_domain != expected_domain:
        problems.extend("missing-domain-role:" + r
                        for r in sorted(expected_domain - present_domain))
    all_ids = [r.id for table in (specialists.ROLES, specialists.EXTRA_ROLES,
                                  specialists.DOMAIN_ROLES) for r in table]
    duplicates = sorted({rid for rid in all_ids if all_ids.count(rid) > 1})
    problems.extend("duplicate-role-id:" + rid for rid in duplicates)
    problems.extend(alias_report()["problems"])
    for source, names in sources.items():
        for name in names:
            rid = resolve_id(name)
            status = ("reconciled" if rid in registry else "excluded-obsolete")
            if status != "reconciled":
                problems.append("%s:%s" % (source, name))
            rows.append({"source": source, "legacy_id": name,
                         "canonical_id": rid, "status": status})
    alias_collisions = [k for k, v in LEGACY_ALIASES.items() if v not in registry]
    problems.extend("alias-collision:" + a for a in alias_collisions)
    return {"ok": not problems, "sources": sources, "rows": rows,
            "problems": problems, "registry_count": len(registry)}


# ------------------------------------------------------------------ permissions

def require_scope(role_id: str, scope: str) -> bool:
    """Contract-based permission check. Prompt text can never grant scope."""
    rec = get(role_id)
    if rec is None:
        raise EngineError("unknown-role:" + role_id)
    if scope not in rec["allowed_scopes"]:
        raise EngineError("scope-denied:%s:%s" % (rec["id"], scope))
    return True


def authorize_tool_request(role_id: str, scope: str,
                           session_id: str = "") -> dict:
    """Authoritative role-based gate for a real ToolRequest.

    Caller-supplied scopes are ignored; only the canonical role contract and
    an authenticated panel session (when supplied) can authorize a tool.
    """
    rec = get(role_id) if str(role_id or "") else None
    if rec is None:
        reason = "role-context-required" if not str(role_id or "") else \
            "unknown-role"
        return {"allowed": False, "reason": reason}
    if scope not in rec["allowed_scopes"]:
        return {"allowed": False, "reason": "scope-denied:" + scope,
                "role_id": rec["id"]}
    principal = ""
    if session_id:
        from core import panelauth
        principal = str(panelauth.principal_for_session(str(session_id)) or "")
        if not principal:
            return {"allowed": False, "reason": "session-invalid",
                    "role_id": rec["id"]}
    return {"allowed": True, "role_id": rec["id"], "scope": scope,
            "principal": principal, "actor_trusted": bool(principal)}


# --------------------------------------------------------------------- handoff

def verify_handoff(project: str, role_id: str,
                   expected: dict[str, str] | None = None) -> dict:
    """Full-text handoff; expected producer hashes are MANDATORY.

    Missing expected hashes fail closed: an unverified artifact is never a
    valid dependency input. Text is returned in full (no truncation).
    """
    rec = get(role_id)
    if rec is None:
        raise EngineError("unknown-role:" + role_id)
    if not isinstance(expected, dict) or not expected:
        return {"role_id": rec["id"], "verified": False, "items": [],
                "problems": ["expected-hash-required"]}
    items, problems = [], []
    for dep in rec["depends_on"]:
        dep_contract = get(dep)
        if dep_contract is None:
            problems.append("unknown-dependency:" + dep)
            continue
        for spec in dep_contract["artifacts"]:
            rel = spec["path"]
            want = expected.get(rel)
            if not want:
                problems.append("expected-hash-required:" + rel)
                continue
            try:
                text = paths.project_path(project, rel).read_text(encoding="utf-8")
            except (OSError, PermissionError, ValueError):
                problems.append("missing:" + rel)
                continue
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if sha != want:
                problems.append("hash-mismatch:" + rel)
            items.append({"path": rel, "sha256": sha, "expected": want,
                          "text": text, "length": len(text)})
    return {"role_id": rec["id"], "verified": not problems,
            "items": items, "problems": problems}


def revalidate_before_consumer(project: str, role_id: str,
                               expected: dict[str, str] | None) -> dict:
    """Re-check artifact hashes immediately before a consumer may proceed."""
    return verify_handoff(project, role_id, expected)


# -------------------------------------------------------------- stale runs

def _proc_start(pid) -> str:
    """Linux process starttime (field 22) — guards against PID reuse."""
    if not pid:
        return ""
    try:
        data = Path("/proc/%d/stat" % int(pid)).read_text(encoding="utf-8")
        return data.rsplit(") ", 1)[1].split()[19]
    except (OSError, ValueError, IndexError, TypeError):
        return ""


def _pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _worker_alive(run: dict) -> bool:
    return _pid_alive(run.get("pid")) and (
        not run.get("proc_start") or
        _proc_start(run.get("pid")) == run["proc_start"])


def _current_run_key(data: dict, project: str, role_id: str) -> str | None:
    """Resolve the single live lease, reopening dead-worker leases."""
    current = None
    for key, run in data["runs"].items():
        if run.get("project") != project or run.get("role_id") != role_id:
            continue
        if run.get("status") != "RUNNING":
            continue
        if not _worker_alive(run):
            run["status"] = "REVISION"
            run["stale_reason"] = "dead-worker-or-pid-reuse"
            continue
        if current is not None:
            # Two live leases is a corruption: fail closed.
            run["status"] = "REVISION"
            run["stale_reason"] = "duplicate-live-lease"
            continue
        current = key
    return current


def start_run(project: str, role_id: str, run_token: str = "",
              supersede: bool = False) -> dict:
    """Atomically claim the single current lease for project+role."""
    rec = get(role_id)
    if rec is None:
        raise EngineError("unknown-role:" + role_id)
    token = str(run_token or secrets.token_hex(12))
    with _engine_lock():
        data = _load_unlocked()
        runs = data["runs"]
        stale_keys = [k for k, r in runs.items()
                      if r.get("project") == project and
                      r.get("role_id") == rec["id"] and
                      r.get("status") == "RUNNING" and not _worker_alive(r)]
        live = _current_run_key(data, project, rec["id"])
        superseded = []
        if live is not None:
            if not supersede:
                raise EngineError("run-already-active:%s:%s" %
                                  (project, rec["id"]))
            runs[live]["status"] = "SUPERSEDED"
            runs[live]["superseded_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            superseded.append(live)
        key = "%s:%s:%s" % (project, rec["id"], token)
        if key in runs:
            raise EngineError("run-token-replay:" + token)
        runs[key] = {"project": project, "role_id": rec["id"],
                     "token": token, "status": "RUNNING",
                     "pid": os.getpid(), "proc_start": _proc_start(os.getpid()),
                     "worker_id": "%d:%s:%s" % (os.getpid(),
                                                _proc_start(os.getpid()), token),
                     "started": time.time(), "superseded": superseded,
                     "stale_resolved": stale_keys}
        _save_unlocked(data)
        result = dict(runs[key])
        result["key"] = key
        return result


@contextmanager
def lease_fence(project: str, role_id: str, run_token: str):
    """Hold the canonical lock while a specialist side effect executes.

    Supersession requires the same lock, so a lease cannot become stale while
    the fenced tool adapter runs. The fence is released only after the side
    effect completes.
    """
    with _engine_lock():
        data = _load_unlocked()
        rid = resolve_id(role_id)
        expected = "%s:%s:%s" % (project, rid, run_token)
        current = _current_run_key(data, project, rid)
        if current != expected:
            raise EngineError("lease-fence-rejected:stale-run")
        yield


def reject_stale(project: str, role_id: str, run_token: str) -> dict:
    """Only the authoritative current lease may produce a result."""
    with _engine_lock():
        data = _load_unlocked()
        rid = resolve_id(role_id)
        key = "%s:%s:%s" % (project, rid, run_token)
        current = _current_run_key(data, project, rid)
        if current != key:
            return {"accepted": False, "reason": "stale-run-rejected"}
        return {"accepted": True, "reason": "current-run", "key": key}


def record_result(project: str, role_id: str, run_token: str,
                  status: str, artifacts: list | None = None) -> dict:
    """Persist a result ONLY from the current lease; stale PASS is blocked."""
    if status not in (opstatus.PASS, opstatus.FAIL, opstatus.BLOCKED,
                      opstatus.SKIP):
        raise EngineError("invalid-result-status:" + status)
    with _engine_lock():
        data = _load_unlocked()
        rid = resolve_id(role_id)
        key = "%s:%s:%s" % (project, rid, run_token)
        current = _current_run_key(data, project, rid)
        if current != key:
            return {"status": opstatus.BLOCKED,
                    "error": "stale-run-rejected"}
        run = data["runs"][key]
        run["status"] = status
        run["finished"] = time.time()
        run["artifacts"] = artifacts or []
        _save_unlocked(data)
        return {"status": status, "key": key}


# ------------------------------------------------------- final response pick

def _turn_time(msg: dict):
    """Trustworthy ordering metadata; never the list position."""
    value = msg.get("completed")
    if value is None and isinstance(msg.get("time"), dict):
        value = msg["time"].get("completed")
    if value is None:
        value = msg.get("created") or msg.get("timestamp")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            from datetime import datetime
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def select_final_response(messages: list) -> dict:
    """Select the NEWEST completed assistant turn by timestamp metadata.

    Input order is never assumed. Aborted/error/incomplete turns are skipped;
    if no trustworthy completed turn exists, return an honest failure.
    """
    if not isinstance(messages, list):
        return {"ok": False, "reason": "messages-not-list", "index": None}
    candidates = []
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("type") != "assistant":
            continue
        finish = str(msg.get("finish") or "").lower()
        outcome = str(msg.get("outcome") or "").lower()
        if finish in ("aborted", "error", "interrupted") or \
                outcome in ("failed", "aborted", "error"):
            continue
        stamp = _turn_time(msg)
        if finish in ("stop", "length") and stamp is not None:
            candidates.append((stamp, idx, msg))
    if not candidates:
        return {"ok": False, "reason": "no-completed-assistant", "index": None}
    stamp, idx, msg = max(candidates, key=lambda item: (item[0], -item[1]))
    return {"ok": True, "reason": "completed-assistant",
            "index": idx, "message": msg, "completed_at": stamp}


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
