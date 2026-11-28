"""AF NEXT — persistent, human-gated specialist workflow state machine.

All state lives in a durable atomic JSON store (never browser localStorage).
A stage may only advance after an explicit user approval bound to an exact
artifact version. Questions and revision history survive refresh/reconnect.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path

import core.config as config
from core import opstatus, paths, workflow_roles

_STORE = config.PROJECT_ROOT / "data" / "workflow_state.json"
_LOCK = threading.RLock()
_ACTIVE: set[str] = set()
SCHEMA_VERSION = "af-workflow-1.0"

RUNNING = "RUNNING"
AWAIT_REVIEW = "AWAITING_USER_REVIEW"
AWAIT_ANSWER = "AWAITING_USER_ANSWER"
REJECTED = "REJECTED"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
PENDING = "PENDING"
APPROVED = "APPROVED"
REVISION = "REVISION"


def _load() -> dict:
    try:
        d = json.loads(_STORE.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return {}
        # Reversible schema enrichment: older records gain defaults in memory
        # and are persisted only on the next legitimate mutation. No data loss.
        for rec in d.values():
            if not isinstance(rec, dict):
                continue
            rec.setdefault("schema_version", SCHEMA_VERSION)
            for st in rec.get("stages") or []:
                st.setdefault("execution_tokens", [])
                st.setdefault("run_pid", None)
                st.setdefault("run_token", "")
        return d
    except (OSError, ValueError):
        return {}


def _save(items: dict) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    tmp.write_text(__import__("json").dumps(items[-200:] if isinstance(items, list)
                                             else items, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(_STORE)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _history(rec: dict, action: str, stage: str = "", detail: str = "") -> None:
    rec["history"].append({"at": _now(), "action": action, "stage": stage,
                           "detail": str(detail)[:300]})
    rec["history"] = rec["history"][-200:]


def _pid_alive(pid: object) -> bool:
    """True only when the recorded stage worker still exists on this host."""
    try:
        value = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    if value == os.getpid():
        return True
    try:
        os.kill(value, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def recover_stale(project: str = "") -> list[str]:
    """Recover RUNNING stages whose worker is gone (e.g. after a crash).

    No artifact or version is removed; only the run lease is cleared and the
    stage is reopened for an explicitly retried execution.
    """
    recovered: list[str] = []
    with _LOCK:
        items = _load()
        changed = False
        for wid, rec in items.items():
            if project and rec.get("project") != project:
                continue
            if wid in _ACTIVE:
                continue
            for st in rec.get("stages") or []:
                if st.get("status") != RUNNING:
                    continue
                if _pid_alive(st.get("run_pid")):
                    continue
                token = str(st.get("run_token") or "stale")
                st["status"] = REVISION
                st["run_pid"] = None
                st["run_token"] = ""
                st.pop("finished_at", None)
                rec["status"] = RUNNING
                rec["current_stage"] = st["id"]
                _history(rec, "stale-run-recovered", st["id"], token)
                recovered.append(st["id"])
                changed = True
        if changed:
            _save(items)
    return recovered


def create(project: str, product_type: str = "", goal: str = "",
           dag: list[dict] | None = None) -> dict:
    with _LOCK:
        items = _load()
        for rec in items.values():
            if rec.get("project") == project:
                return rec
        dag = dag or workflow_roles.dag_for(product_type, goal)
        wid = "wf-" + hashlib.sha256((project + str(time.time())).encode()).hexdigest()[:12]
        rec = {
            "id": wid, "project": project, "product_type": product_type,
            "goal": str(goal)[:1200], "dag": [dict(s) for s in dag],
            "stages": [{"id": s["id"], "role": s["role"], "title": s["title"],
                        "kind": s["kind"], "tool": s.get("tool"),
                        "deps": list(s["deps"]), "status": PENDING,
                        "versions": [], "approvals": [], "approved_version": 0,
                        "question": None, "answer": None, "error": "",
                        "execution_tokens": [], "last_result": None,
                        "started_at": "", "finished_at": ""} for s in dag],
            "current_stage": dag[0]["id"] if dag else "",
            "status": RUNNING if dag else COMPLETED,
            "history": [], "rev": 1, "created_at": _now(), "updated_at": _now(),
        }
        _history(rec, "workflow-created", rec["current_stage"],
                 "%d stages" % len(dag))
        items[wid] = rec
        _save(items)
        return rec


def by_project(project: str) -> dict | None:
    with _LOCK:
        for rec in _load().values():
            if rec.get("project") == project:
                return rec
    return None


def get(wid: str) -> dict | None:
    with _LOCK:
        return _load().get(wid)


def _mutate(wid: str, fn):
    with _LOCK:
        items = _load()
        rec = items.get(wid)
        if rec is None:
            return {"status": opstatus.BLOCKED, "error": "workflow-not-found"}
        result = fn(rec, items)
        rec["rev"] = int(rec.get("rev", 0)) + 1
        rec["updated_at"] = _now()
        _save(items)
        return result


def _stage(rec: dict, sid: str) -> dict | None:
    return next((s for s in rec["stages"] if s["id"] == sid), None)


def _current(rec: dict) -> dict | None:
    return _stage(rec, rec.get("current_stage", ""))


def sha256_file(rel: str, project: str) -> str:
    p = paths.project_path(project, rel)
    return hashlib.sha256(p.read_bytes()).hexdigest()


def verify_integrity(project: str, persist: bool = True) -> dict | None:
    """An artifact change invalidates the approval bound to its old version."""
    rec = by_project(project)
    if rec is None:
        return None
    problems = []
    for s in rec["stages"]:
        if s.get("status") != APPROVED or not s.get("approved_version"):
            continue
        v = next((x for x in s["versions"]
                  if x["version"] == s["approved_version"]), None)
        if not v:
            continue
        bad = False
        for art in v.get("artifacts") or []:
            rel = art.get("path")
            if not rel:
                continue
            try:
                now = sha256_file(rel, project)
            except (OSError, PermissionError, FileNotFoundError):
                problems.append({"stage": s["id"], "path": rel,
                                 "issue": "missing"})
                bad = True
                continue
            if now != art.get("sha256"):
                problems.append({"stage": s["id"], "path": rel,
                                 "issue": "hash-changed"})
                bad = True
        if bad and persist:
            def op(r, items):
                st = _stage(r, s["id"])
                for v in st["versions"]:
                    if v["version"] == st["approved_version"]:
                        v["invalidated"] = True
                st["status"] = REVISION
                st["approvals"] = []
                st["approved_version"] = 0
                r["status"] = RUNNING
                r["current_stage"] = s["id"]
                _history(r, "approval-invalidated", s["id"],
                         "artifact changed after approval; new version required")
                return {"status": "ok"}
            _mutate(rec["id"], op)
    return {"status": "ok", "problems": problems}


def guard(project: str, stage_id: str, token: str = "") -> dict:
    """The ONLY way to attempt a stage — fail closed on every pause reason."""
    recover_stale(project)
    rec = by_project(project)
    if rec is None:
        return {"status": opstatus.BLOCKED, "error": "no-workflow"}
    stage = _stage(rec, stage_id)
    # Replayed delivery of an already-executed token is idempotent even if the
    # workflow has since moved to a review pause — never run it twice.
    if stage and token and token in stage["execution_tokens"]:
        return {"status": opstatus.PASS, "ok": True, "idempotent": True,
                "result": stage.get("last_result")}
    if rec["status"] == AWAIT_REVIEW:
        return {"status": opstatus.BLOCKED, "error": "awaiting-user-review",
                "stage": rec["current_stage"]}
    if rec["status"] == AWAIT_ANSWER:
        return {"status": opstatus.BLOCKED, "error": "awaiting-user-answer",
                "stage": rec["current_stage"]}
    if rec["status"] in (COMPLETED, FAILED, REJECTED):
        return {"status": opstatus.BLOCKED, "error": "workflow-" + rec["status"].lower()}
    if rec["id"] in _ACTIVE:
        return {"status": opstatus.BLOCKED, "error": "stage-already-running"}
    s = _stage(rec, stage_id)
    if s is None:
        return {"status": opstatus.BLOCKED, "error": "unknown-stage"}
    if stage_id != rec.get("current_stage"):
        return {"status": opstatus.BLOCKED, "error": "not-current-stage"}
    if s["status"] not in (PENDING, REVISION):
        return {"status": opstatus.BLOCKED, "error": "stage-" + s["status"].lower()}
    for dep in s["deps"]:
        d = _stage(rec, dep)
        if not d or d["status"] != APPROVED or not d.get("approved_version"):
            return {"status": opstatus.BLOCKED, "error": "dependency-not-approved:" + dep}
    return {"status": "ok", "ok": True, "wid": rec["id"]}


def run_stage(project: str, stage_id: str, token: str = "", executor=None,
              goal: str = "") -> dict:
    """Execute ONE stage; on success STOP and wait for explicit user review."""
    g = guard(project, stage_id, token)
    if g.get("status") == opstatus.PASS and g.get("idempotent"):
        return g
    if g.get("status") != "ok":
        return g
    wid = g["wid"]
    # Atomic claim: guard() checked _ACTIVE before this line, so two callers
    # could both pass. Claim the stage under the same lock before any work.
    with _LOCK:
        if wid in _ACTIVE:
            return {"status": opstatus.BLOCKED,
                    "error": "stage-already-running"}
        _ACTIVE.add(wid)
    rec = get(wid)
    s = _stage(rec, stage_id)
    def begin(r, items):
        st = _stage(r, stage_id)
        st["status"] = RUNNING
        st["started_at"] = _now()
        st["error"] = ""
        st["run_pid"] = os.getpid()
        st["run_token"] = str(token or secrets.token_hex(8))
        r["status"] = RUNNING
        _history(r, "stage-started", stage_id, st["run_token"])
        return {"status": "ok"}
    _mutate(wid, begin)
    try:
        if executor is None:
            from core import workflow_executor
            executor = workflow_executor.run
        result = executor(project, dict(s), goal or rec.get("goal", ""))
        result = result if isinstance(result, dict) else {
            "status": opstatus.FAIL, "error": "invalid-executor-result"}
        rg = result.get("status") or opstatus.FAIL
        artifacts = result.get("artifacts") or []
        if rg != opstatus.PASS or not artifacts:
            def failed(r, items):
                st = _stage(r, stage_id)
                st["status"] = FAILED
                st["error"] = str(result.get("error") or "no-real-artifact")[:400]
                st["finished_at"] = _now()
                st["run_pid"] = None
                st["run_token"] = ""
                r["status"] = FAILED
                _history(r, "stage-failed", stage_id, st["error"])
                return {"status": opstatus.FAIL, "error": st["error"]}
            return _mutate(wid, failed)
        def done(r, items):
            st = _stage(r, stage_id)
            version = len(st["versions"]) + 1
            art = []
            for a in artifacts:
                rel = str(a.get("path") or "")
                try:
                    sha = a.get("sha256") or sha256_file(rel, project)
                except (OSError, PermissionError, FileNotFoundError):
                    sha = ""
                if not sha:
                    return {"status": opstatus.FAIL,
                            "error": "artifact-hash-unavailable:" + rel}
                art.append({"path": rel, "sha256": sha,
                            "kind": a.get("kind") or st["kind"],
                            "size": a.get("size"), "note": a.get("note", "")[:200]})
            st["versions"].append({"version": version, "artifacts": art,
                                   "at": _now(), "evidence": result.get("evidence") or {},
                                   "note": str(result.get("note") or "")[:300]})
            st["status"] = AWAIT_REVIEW
            st["finished_at"] = _now()
            st["run_pid"] = None
            st["run_token"] = ""
            st["last_result"] = {"status": rg, "version": version, "artifacts": art}
            if token and token not in st["execution_tokens"]:
                st["execution_tokens"].append(token)
            r["status"] = AWAIT_REVIEW
            _history(r, "stage-awaiting-review", stage_id, "v%d" % version)
            return {"status": opstatus.PASS, "ok": True, "version": version,
                    "artifacts": art, "workflow_status": AWAIT_REVIEW}
        return _mutate(wid, done)
    except Exception as exc:  # noqa: BLE001 — real failure is never PASS
        def crashed(r, items):
            st = _stage(r, stage_id)
            st["status"] = FAILED
            st["error"] = str(exc)[:400]
            st["run_pid"] = None
            st["run_token"] = ""
            r["status"] = FAILED
            _history(r, "stage-exception", stage_id, str(exc)[:200])
            return {"status": opstatus.FAIL, "error": str(exc)[:400]}
        return _mutate(wid, crashed)
    finally:
        _ACTIVE.discard(wid)


def _advance(r: dict) -> None:
    nxt = None
    for s in r["stages"]:
        if s["status"] in (PENDING, REVISION) and all(
                (d := _stage(r, dep)) and d["status"] == APPROVED
                for dep in s["deps"]):
            nxt = s["id"]
            break
    if nxt:
        r["current_stage"] = nxt
        r["status"] = RUNNING
    else:
        r["current_stage"] = ""
        r["status"] = COMPLETED


def approve(project: str, stage_id: str, version: int, approval_id: str) -> dict:
    verify_integrity(project)
    def op(r, items):
        s = _stage(r, stage_id)
        if r["status"] != AWAIT_REVIEW or not s or s["status"] != AWAIT_REVIEW:
            return {"status": opstatus.BLOCKED, "error": "not-awaiting-review"}
        v = int(version)
        cur = len(s["versions"])
        if v != cur:
            return {"status": opstatus.FAIL, "error": "version-invalidated:%s!=%s" % (v, cur)}
        if s["versions"][v - 1].get("invalidated"):
            return {"status": opstatus.FAIL, "error": "version-invalidated:hash-changed"}
        if any(a.get("id") == approval_id for a in s["approvals"]):
            return {"status": opstatus.BLOCKED, "error": "duplicate-approval"}
        s["approvals"].append({"id": str(approval_id)[:80], "version": v, "at": _now()})
        s["approved_version"] = v
        s["status"] = APPROVED
        s["finished_at"] = _now()
        _history(r, "stage-approved", stage_id, "%s v%d" % (approval_id, v))
        _advance(r)
        return {"status": opstatus.PASS, "ok": True, "workflow_status": r["status"],
                "next_stage": r["current_stage"]}
    rec = by_project(project)
    if rec is None:
        return {"status": opstatus.BLOCKED, "error": "no-workflow"}
    return _mutate(rec["id"], op)


def reject(project: str, stage_id: str, note: str = "") -> dict:
    def op(r, items):
        s = _stage(r, stage_id)
        if r["status"] != AWAIT_REVIEW or not s or s["status"] != AWAIT_REVIEW:
            return {"status": opstatus.BLOCKED, "error": "not-awaiting-review"}
        s["status"] = REJECTED
        r["status"] = REJECTED
        _history(r, "stage-rejected", stage_id, note)
        return {"status": opstatus.PASS, "ok": True, "workflow_status": REJECTED}
    rec = by_project(project)
    if rec is None:
        return {"status": opstatus.BLOCKED, "error": "no-workflow"}
    return _mutate(rec["id"], op)


def revise(project: str, stage_id: str, note: str = "") -> dict:
    def op(r, items):
        s = _stage(r, stage_id)
        if not s or s["status"] not in (REJECTED, AWAIT_REVIEW):
            return {"status": opstatus.BLOCKED, "error": "stage-not-revisable"}
        s["status"] = REVISION
        # Live acceptance: full user revision requirements must reach the model.
        s["note"] = str(note)[:4000]
        r["status"] = RUNNING
        r["current_stage"] = stage_id
        _history(r, "revision-requested", stage_id, note)
        return {"status": opstatus.PASS, "ok": True, "workflow_status": RUNNING}
    rec = by_project(project)
    if rec is None:
        return {"status": opstatus.BLOCKED, "error": "no-workflow"}
    return _mutate(rec["id"], op)


def record_external_version(project: str, stage_id: str, rel: str,
                            sha256: str, note: str = "") -> dict:
    """Append a NEW immutable version produced by a governed critique revision.

    Never rewrites an existing version. The caller (core.critique) has already
    written the versioned file and computed its real SHA-256.
    """
    rec = by_project(project)
    if rec is None:
        return {"status": opstatus.BLOCKED, "error": "no-workflow"}
    def op(r, items):
        s = _stage(r, stage_id)
        if s is None:
            return {"status": opstatus.BLOCKED, "error": "unknown-stage"}
        if not sha256:
            return {"status": opstatus.FAIL, "error": "artifact-hash-unavailable"}
        version = len(s["versions"]) + 1
        s["versions"].append({
            "version": version,
            "artifacts": [{"path": rel, "sha256": sha256,
                           "kind": s.get("kind", "code"),
                           "note": str(note)[:200]}],
            "at": _now(), "evidence": {"producer": "critique-revision"},
            "note": str(note)[:300]})
        s["status"] = AWAIT_REVIEW
        s["finished_at"] = _now()
        r["status"] = AWAIT_REVIEW
        r["current_stage"] = stage_id
        _history(r, "revision-version-added", stage_id, "v%d" % version)
        return {"status": opstatus.PASS, "ok": True, "version": version}
    return _mutate(rec["id"], op)


def amend_artifact_hash(project: str, stage_id: str, version: int,
                        path: str, sha256: str, reason: str) -> dict:
    """Record a formatting-only amendment without creating a new version."""
    def op(r, items):
        s = _stage(r, stage_id)
        if s is None:
            return {"status": opstatus.BLOCKED, "error": "unknown-stage"}
        v = next((x for x in s["versions"] if x["version"] == int(version)), None)
        if v is None:
            return {"status": opstatus.BLOCKED, "error": "unknown-version"}
        art = next((a for a in v["artifacts"] if a["path"] == path), None)
        if art is None:
            return {"status": opstatus.BLOCKED, "error": "unknown-artifact"}
        old = art.get("sha256", "")
        art["sha256"] = str(sha256)[:64]
        art["amended"] = True
        art["amend_reason"] = str(reason)[:300]
        _history(r, "artifact-amended", stage_id,
                 "%s v%d %s->%s" % (path, version, old[:8], str(sha256)[:8]))
        return {"status": opstatus.PASS, "ok": True, "old_sha256": old}
    rec = by_project(project)
    if rec is None:
        return {"status": opstatus.BLOCKED, "error": "no-workflow"}
    return _mutate(rec["id"], op)


def retry_failed(project: str, stage_id: str, note: str = "") -> dict:
    """Live acceptance fix: a failed real specialist attempt can be retried.

    Failure is never converted to PASS; only the SAME specialist is reopened
    and every previous successful version stays untouched.
    """
    def op(r, items):
        s = _stage(r, stage_id)
        if not s or s["status"] != FAILED or r["status"] != FAILED:
            return {"status": opstatus.BLOCKED, "error": "stage-not-failed"}
        s["status"] = REVISION
        if note:
            s["note"] = str(note)[:4000]
        r["status"] = RUNNING
        r["current_stage"] = stage_id
        _history(r, "retry-requested", stage_id, note)
        return {"status": opstatus.PASS, "ok": True, "workflow_status": RUNNING}
    rec = by_project(project)
    if rec is None:
        return {"status": opstatus.BLOCKED, "error": "no-workflow"}
    return _mutate(rec["id"], op)


def set_goal(project: str, goal: str) -> dict:
    """Live acceptance fix: a user's revised goal must reach later specialists."""
    def op(r, items):
        r["goal"] = str(goal)[:4000]
        _history(r, "goal-updated", r.get("current_stage", ""), str(goal)[:200])
        return {"status": opstatus.PASS, "ok": True, "workflow_status": r["status"]}
    rec = by_project(project)
    if rec is None:
        return {"status": opstatus.BLOCKED, "error": "no-workflow"}
    return _mutate(rec["id"], op)


def ask(project: str, stage_id: str, question: str, options: list[str],
        allow_other: bool = False, question_id: str = "") -> dict:
    def op(r, items):
        s = _stage(r, stage_id)
        if r["status"] != RUNNING or not s or s["status"] not in (
                PENDING, REVISION, RUNNING):
            return {"status": opstatus.BLOCKED, "error": "stage-not-running"}
        qid = str(question_id or "q-" + secrets.token_hex(6))[:80]
        s["question"] = {"id": qid, "text": str(question)[:600],
                         "options": [str(o)[:200] for o in options][:8],
                         "allow_other": bool(allow_other), "at": _now(),
                         "answered": False}
        s["answer"] = None
        r["status"] = AWAIT_ANSWER
        _history(r, "question-asked", stage_id, qid)
        return {"status": opstatus.PASS, "ok": True, "question_id": qid,
                "workflow_status": AWAIT_ANSWER}
    rec = by_project(project)
    if rec is None:
        return {"status": opstatus.BLOCKED, "error": "no-workflow"}
    return _mutate(rec["id"], op)


def answer(project: str, stage_id: str, value: str, question_id: str) -> dict:
    def op(r, items):
        s = _stage(r, stage_id)
        if r["status"] != AWAIT_ANSWER or not s or not s.get("question"):
            return {"status": opstatus.BLOCKED, "error": "not-awaiting-answer"}
        q = s["question"]
        if q["id"] != question_id:
            return {"status": opstatus.FAIL, "error": "question-id-mismatch"}
        if q["answered"]:
            return {"status": opstatus.BLOCKED, "error": "question-already-answered"}
        val = str(value)[:1200]
        if val not in q["options"] and not q["allow_other"]:
            return {"status": opstatus.FAIL, "error": "answer-not-in-options"}
        q["answered"] = True
        s["answer"] = val
        r["status"] = RUNNING
        _history(r, "question-answered", stage_id, val[:120])
        return {"status": opstatus.PASS, "ok": True, "workflow_status": RUNNING}
    rec = by_project(project)
    if rec is None:
        return {"status": opstatus.BLOCKED, "error": "no-workflow"}
    return _mutate(rec["id"], op)


def state(project: str) -> dict | None:
    recover_stale(project)
    rec = by_project(project)
    if rec is None:
        return None
    verify_integrity(project)
    rec = by_project(project)
    return {
        "id": rec["id"], "project": project, "status": rec["status"],
        "current_stage": rec["current_stage"], "rev": rec["rev"],
        "updated_at": rec["updated_at"], "goal": rec.get("goal", ""),
        "stages": [{"id": s["id"], "role": s["role"], "title": s["title"],
                    "kind": s["kind"], "deps": s["deps"], "status": s["status"],
                    "versions": s["versions"], "approved_version": s["approved_version"],
                    "approvals": s["approvals"], "question": s["question"],
                    "answer": s["answer"], "error": s["error"],
                    "note": s.get("note", "")}
                   for s in rec["stages"]],
        "history": rec["history"][-40:],
        "pause_reason": ("user-review" if rec["status"] == AWAIT_REVIEW else
                         "user-answer" if rec["status"] == AWAIT_ANSWER else ""),
    }


def events(project: str, since_rev: int = 0) -> list[dict]:
    rec = by_project(project)
    if rec is None or int(rec.get("rev", 0)) <= int(since_rev):
        return []
    return [state(project)]
