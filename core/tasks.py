"""P7-2 — persistent task queue for the multi-agent orchestrator.

Design (mobile-first, stdlib-only):
  * ONE store file ``data/tasks.json`` (0600, atomic tmp+replace) — survives
    panel restarts, which is the whole point of the queue.
  * All reads/writes under a single module lock; claim is the ONLY transition
    queued→running, so two workers can never take the same task.
  * States: queued → running → done | failed | cancelled.
    failed tasks automatically go back to queued while attempts remain.
  * Recovery: tasks found ``running`` after a process restart are orphans;
    ``recover_orphans()`` re-queues (or fails) them honestly.

The queue never talks to the model and never touches projects' files —
it is pure bookkeeping.
"""

import json
import os
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

import core.config as config
from core import paths

_lock = threading.RLock()
_STORE = config.PROJECT_ROOT / "data" / "tasks.json"

_STATES = ("queued", "running", "done", "failed", "cancelled", "awaiting_user")


class TaskError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _now() -> float:
    return time.time()


def _iso(ts: float | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _load() -> list[dict]:
    try:
        data = json.loads(_STORE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(tasks: list[dict]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    try:
        _STORE.parent.chmod(0o700)
    except OSError:
        pass
    tmp = _STORE.with_name(_STORE.name + ".tmp")
    tmp.write_text(json.dumps(tasks, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(_STORE)


def _get(tasks: list[dict], task_id: str) -> dict | None:
    return next((t for t in tasks if t.get("id") == task_id), None)


def _validated_project(project: str) -> Path:
    try:
        return paths.project_dir(project)
    except (PermissionError, FileNotFoundError) as exc:
        raise TaskError(400, str(exc))


# ------------------------------------------------------------------ CRUD ----

def add_task(project: str, goal: str, agent: str = "builder",
             max_attempts: int = 2, kind: str = "generate",
             meta: dict | None = None) -> dict:
    """AF-18: `kind` distinguishes queue consumers ("generate" = legacy
    build task, "pipeline" = integrated request→delivery); `meta` carries
    extra keys (e.g. pipeline_id). Legacy tasks default unchanged."""
    if not (goal or "").strip():
        raise TaskError(400, "هدف وظیفه خالی است")
    if len(goal) > 20000:
        raise TaskError(400, "هدف وظیفه بیش از حد بلند است (۲۰۰۰۰ کاراکتر)")
    _validated_project(project)
    from core import agents as agentsmod
    try:
        a = agentsmod.get_agent(agent)
    except agentsmod.AgentError as exc:
        raise TaskError(exc.status, exc.message)
    if not a.get("enabled"):
        raise TaskError(409, f"عامل «{agent}» غیرفعال است")
    with _lock:
        tasks = _load()
        task = {
            "id": "t-%s-%s" % (datetime.now().strftime("%H%M%S"),
                               secrets.token_hex(2)),
            "project": project, "agent": agent, "goal": goal.strip(),
            "kind": str(kind or "generate")[:20],
            "meta": meta if isinstance(meta, dict) else None,
            "hold": None,
            "state": "queued", "claimed_by": None,
            "attempts": 0,
            "max_attempts": max(1, min(int(max_attempts), 5)),
            "error": None, "result": None,
            "created": _now(), "started": None, "finished": None,
            "updated": _now(),
        }
        tasks.append(task)
        _save(tasks)
    return task


def list_tasks(state: str | None = None) -> list[dict]:
    with _lock:
        tasks = _load()
    if state:
        tasks = [t for t in tasks if t.get("state") == state]
    tasks.sort(key=lambda t: t.get("created") or 0, reverse=True)
    return tasks


def get_task(task_id: str) -> dict:
    with _lock:
        t = _get(_load(), task_id)
    if not t:
        raise TaskError(404, "وظیفه یافت نشد: " + task_id)
    return t


def peek_next_queued() -> dict | None:
    """Read-only copy of the oldest queued task (P7-4: lets a worker SKIP
    tasks of a busy project BEFORE claiming — no attempts burned, no
    claim/release churn). Never mutates the store."""
    with _lock:
        tasks = _load()
        tasks.sort(key=lambda t: t.get("created") or 0)
        for t in tasks:
            if t.get("state") == "queued" and not t.get("hold"):
                return dict(t)
    return None


def claim_next(worker_id: str) -> dict | None:
    """Atomically take the oldest queued task → running (FIFO).
    AF-18: tasks on HOLD (e.g. a pipeline waiting for plan approval) are
    never claimed — the hold must be explicitly cleared."""
    with _lock:
        tasks = _load()
        tasks.sort(key=lambda t: t.get("created") or 0)
        for t in tasks:
            if t.get("state") == "queued" and not t.get("hold"):
                t["state"] = "running"
                t["claimed_by"] = worker_id
                t["attempts"] = int(t.get("attempts") or 0) + 1
                t["started"] = _now()
                t["updated"] = _now()
                _save(tasks)
                return t
    return None


def set_hold(task_id: str, hold: str | None) -> dict:
    """AF-18 — pause/unpause a task at a human gate. A held task stays
    `queued` but is skipped by claim_next until the hold is cleared."""
    with _lock:
        tasks = _load()
        t = _get(tasks, task_id)
        if not t:
            raise TaskError(404, "وظیفه یافت نشد: " + task_id)
        if t["state"] not in ("queued", "running"):
            raise TaskError(409, "فقط وظیفهٔ در انتظار/در حال اجرا قابل توقف است")
        t["hold"] = str(hold) if hold else None
        t["updated"] = _now()
        _save(tasks)
        return t


def release_task(task_id: str, note: str = "") -> dict:
    """Running → queued WITHOUT burning an attempt (graceful give-back)."""
    with _lock:
        tasks = _load()
        t = _get(tasks, task_id)
        if not t:
            raise TaskError(404, "وظیفه یافت نشد: " + task_id)
        if t["state"] != "running":
            raise TaskError(409, "فقط وظیفهٔ در حال اجرا قابل آزادسازی است")
        t["state"] = "queued"
        t["claimed_by"] = None
        if note:
            t["error"] = str(note)[:500]
        t["updated"] = _now()
        _save(tasks)
        return t


def finish_task(task_id: str, result: dict | None = None) -> dict:
    with _lock:
        tasks = _load()
        t = _get(tasks, task_id)
        if not t:
            raise TaskError(404, "وظیفه یافت نشد: " + task_id)
        # AF-35 — terminal cancellation must never be overwritten by a
        # worker that was already running when the user cancelled.
        if t["state"] in ("done", "cancelled"):
            return t
        t["state"] = "done"
        t["result"] = result or {}
        t["error"] = None
        t["finished"] = t["updated"] = _now()
        _save(tasks)
        return t


def fail_task(task_id: str, error: str, retry: bool = True,
              result: dict | None = None) -> dict:
    """Mark failure; re-queue automatically while attempts remain."""
    with _lock:
        tasks = _load()
        t = _get(tasks, task_id)
        if not t:
            raise TaskError(404, "وظیفه یافت نشد: " + task_id)
        # A terminal task cannot be resurrected by a late worker exception.
        if t["state"] in ("done", "cancelled"):
            return t
        t["error"] = str(error or "")[:800]
        t["updated"] = _now()
        if result is not None:
            t["result"] = result
        if retry and int(t.get("attempts") or 0) < int(t.get("max_attempts") or 1):
            t["state"] = "queued"
            t["claimed_by"] = None
        else:
            t["state"] = "failed"
            t["finished"] = t["updated"] = _now()
        _save(tasks)
        return t


def cancel_task(task_id: str) -> dict:
    with _lock:
        tasks = _load()
        t = _get(tasks, task_id)
        if not t:
            raise TaskError(404, "وظیفه یافت نشد: " + task_id)
        if t["state"] in ("done", "cancelled"):
            raise TaskError(409, "وظیفه در وضعیت پایانی است")
        t["state"] = "cancelled"
        t["claimed_by"] = None
        t["finished"] = t["updated"] = _now()
        _save(tasks)
        return t


def retry_task(task_id: str) -> dict:
    """Manual re-queue of a failed/cancelled task (does not reset attempts)."""
    with _lock:
        tasks = _load()
        t = _get(tasks, task_id)
        if not t:
            raise TaskError(404, "وظیفه یافت نشد: " + task_id)
        if t["state"] not in ("failed", "cancelled"):
            raise TaskError(409, "فقط وظیفهٔ شکست‌خورده/لغوشده قابل تلاش مجدد است")
        if int(t.get("attempts") or 0) >= int(t.get("max_attempts") or 1):
            t["max_attempts"] = int(t.get("attempts") or 0) + 1  # explicit manual retry buys one more
        t["state"] = "queued"
        t["claimed_by"] = None
        t["error"] = None
        t["result"] = None
        t["finished"] = None
        t["hold"] = None
        # A trusted manual retry is allowed to resume a pipeline that was
        # previously marked failed, keeping its completed `done` stages.
        meta = dict(t.get("meta") or {})
        meta["resume_failed"] = True
        t["meta"] = meta
        t["updated"] = _now()
        _save(tasks)
        return t


def ask_task(task_id: str, question: str, options: list, token: str,
             decision: str = "", context: dict | None = None) -> dict:
    """AF-36 — park a task on a real human decision (ASK/CONFIRM).

    A one-shot confirmation token from core.confirmations is stored on the
    task; only answer_task with that exact, unexpired token can resume it.
    """
    with _lock:
        tasks = _load()
        t = _get(tasks, task_id)
        if not t:
            raise TaskError(404, "وظیفه یافت نشد: " + task_id)
        if t["state"] in ("done", "failed", "cancelled"):
            raise TaskError(409, "وظیفه در وضعیت پایانی است")
        t["state"] = "awaiting_user"
        t["claimed_by"] = None
        t["hold"] = None
        t["error"] = None
        t["finished"] = None
        t["ask_answer"] = None       # a previous answer never applies to a new ASK
        t["ask"] = {"question": str(question)[:600],
                    "options": [str(x)[:120] for x in (options or [])][:6],
                    "token": str(token), "decision": str(decision),
                    "context": context or {}, "created": _now()}
        t["updated"] = _now()
        _save(tasks)
        return t


def answer_task(task_id: str, token: str, answer: str) -> dict:
    """Resume an awaiting task ONLY with its valid one-shot token."""
    from core import confirmations
    from core.confirmations import ConfirmError
    with _lock:
        tasks = _load()
        t = _get(tasks, task_id)
        if not t or t.get("state") != "awaiting_user":
            raise TaskError(409, "وظیفه در انتظار سؤال نیست")
        ask = t.get("ask") or {}
        if not token or token != ask.get("token"):
            raise TaskError(403, "توکن پاسخ نامعتبر است")
        try:
            confirmations.consume(token)
        except ConfirmError as exc:
            raise TaskError(exc.status, exc.message)
        t["state"] = "queued"
        t["claimed_by"] = None
        t["hold"] = None
        t["ask_answer"] = str(answer)[:600]
        t["ask"]["answered"] = _now()
        t["updated"] = _now()
        _save(tasks)
        return t


def archive_asks(task_ids: list[str], note: str = "") -> dict:
    """AF-54 — mark old/orphan ASKs as archived WITHOUT deleting any data.

    The task record, question, token and context stay intact; only a
    non-destructive metadata flag is added so the active decision center can
    stay calm. No task is answered or resumed here.
    """
    ids = {str(x) for x in (task_ids or []) if str(x)}
    with _lock:
        tasks = _load()
        changed = []
        for t in tasks:
            if t.get("id") not in ids:
                continue
            ask = t.get("ask")
            if not isinstance(ask, dict):
                continue
            ask["archived"] = True
            ask["archived_at"] = _now()
            if note:
                ask["archive_note"] = str(note)[:200]
            changed.append(t["id"])
        if changed:
            _save(tasks)
    return {"archived": changed, "count": len(changed)}


def recover_orphans() -> list[dict]:
    """Re-queue tasks stuck in `running` from a dead process (real restart
    recovery). Returns the recovered tasks for honest reporting."""
    with _lock:
        tasks = _load()
        out = []
        for t in tasks:
            if t.get("state") == "running":
                if int(t.get("attempts") or 0) < int(t.get("max_attempts") or 1):
                    t["state"] = "queued"
                    t["error"] = "بازیابی پس از ری‌استارت (وظیفهٔ نیمه‌تمام)"
                else:
                    t["state"] = "failed"
                    t["finished"] = t["updated"] = _now()
                    t["error"] = "پس از ری‌استارت؛ سقف تلاش پر بود"
                t["claimed_by"] = None
                t["hold"] = None
                t["result"] = None
                t["finished"] = None
                t["updated"] = _now()
                out.append(t)
        if out:
            _save(tasks)
        return out


def stats() -> dict:
    with _lock:
        tasks = _load()
    out = {s: 0 for s in _STATES}
    for t in tasks:
        out[t.get("state", "?")] = out.get(t.get("state", "?"), 0) + 1
    out["total"] = len(tasks)
    return out


def _iso_view(t: dict) -> dict:
    """Human-facing copy with ISO timestamps (never mutate the store)."""
    v = dict(t)
    if isinstance(v.get("ask"), dict):
        v["ask"] = {k: x for k, x in v["ask"].items() if k != "token"}
    for k in ("created", "started", "finished", "updated"):
        v[k] = _iso(t.get(k))
    return v


def view(tasks: list[dict]) -> list[dict]:
    return [_iso_view(t) for t in tasks]
