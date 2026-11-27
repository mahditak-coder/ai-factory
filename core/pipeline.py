"""AF-18 — integrated request→delivery pipeline (orchestration ONLY).

Design rules (built ON the existing proven parts — nothing rebuilt):
  * the EXISTING task queue (tasks.json) carries pipeline work as a new
    task kind ("pipeline"); the EXISTING orchestrator worker executes it;
  * stages call the EXISTING modules: factory.plan/generate,
    factory.testrun_combined, autofix.run (quality+fix+post-test+rollback),
    release.build_release — plus ONE genuinely missing capability:
    independent delivery validation (extract outside projects/, boot the
    bundle via its own run.sh, probe health, stop);
  * stage transitions persist to ``data/pipelines.json`` (0600, atomic,
    same durability pattern as tasks.json) — a restarted worker resumes at
    the recorded stage and NEVER repeats a completed stage;
  * human gates are REAL: after planning the pipeline holds (task hold)
    until /api/pipeline/approve; rejection is terminal and honest;
  * every failure stops the pipeline with the REAL reason — no stage is
    ever reported green without its gate passing.
"""

import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

import core.config as config
from core import paths, requestcheck, tasks as tasksmod

log = logging.getLogger("ai-factory.pipeline")

_lock = threading.RLock()
_STORE = config.PROJECT_ROOT / "data" / "pipelines.json"

WAIT_S = 480          # model turn ceiling (same as orchestrator TASK_TIMEOUT_S)
# AF-32 — the old hard "3 attempts" rule is no longer the main stop signal.
# Continuation is driven by real progress via agent_loop; this is the pipeline
# safety ceiling (agent_loop has a secondary hard cap and a wall-clock budget).
FIX_MAX = 6
HISTORY_MAX = 100     # per-pipeline bounded event log (oldest evict)

STAGES = ("plan", "generate", "deps", "test", "quality", "release", "deliver")
# types the pipeline may carry end-to-end
# AF-22: android now rides the unified pipeline too — its build/sign/verify
# stages run through core/engines/android.py (the build IS the compile test);
# there is no server to boot, so the test/quality/release/deliver stages get
# an android-specific branch below.
SUPPORTED_TYPES = set(config.PROJECT_TYPE_SPECS)


class PipelineError(Exception):
    def __init__(self, status: int, message: str, result: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.result = result


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


def _save(recs: list[dict]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    try:
        _STORE.parent.chmod(0o700)
    except OSError:
        pass
    tmp = _STORE.with_name(_STORE.name + ".tmp")
    tmp.write_text(json.dumps(recs, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(_STORE)


def _get(recs: list[dict], pid: str) -> dict | None:
    return next((r for r in recs if r.get("id") == pid), None)


def _persist(rec: dict) -> dict:
    with _lock:
        recs = _load()
        cur = _get(recs, rec["id"])
        if cur is not None:
            recs[recs.index(cur)] = rec
        else:
            recs.insert(0, rec)
        _save(recs)
    return dict(rec)


def get(pid: str) -> dict:
    with _lock:
        r = _get(_load(), pid)
    if not r:
        raise PipelineError(404, "خط لوله یافت نشد: " + pid)
    return dict(r)


def list_pipelines(state: str | None = None) -> list[dict]:
    with _lock:
        recs = _load()
    if state:
        recs = [r for r in recs if r.get("state") == state]
    recs.sort(key=lambda r: r.get("created") or 0, reverse=True)
    return recs


def _ev(rec: dict, note: str) -> None:
    h = list(rec.get("history") or [])
    h.insert(0, {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "stage": rec.get("stage"), "state": rec.get("state"),
                 "note": str(note)[:220]})
    rec["history"] = h[:HISTORY_MAX]


def _view(rec: dict) -> dict:
    v = dict(rec)
    v["created"] = _iso(rec.get("created"))
    v["updated"] = _iso(rec.get("updated"))
    return v


# ------------------------------------------------------------------ create ----

def _model_summary(project: str) -> dict:
    """AF-39 — bounded Project Model facts attached to a pipeline record."""
    try:
        from core import project_snapshot
        snap = project_snapshot.build(project)
        sec = snap.get("sections") or {}
        return {"status": snap.get("status"),
                "entry": (sec.get("entry") or {}).get("path"),
                "stack": (sec.get("stack") or {}).get("language"),
                "commands": (sec.get("commands") or {}).get("items"),
                "architecture_edges": len(((sec.get("architecture") or {})
                                            .get("edges") or {}).get("value") or []),
                "git": (sec.get("git") or {}).get("status"),
                "environment": {k: (v or {}).get("value")
                                for k, v in ((sec.get("environment") or {})
                                             .get("capabilities") or {}).items()}}
    except Exception:  # noqa: BLE001 — optional
        return {"status": "unavailable"}


def _code_impact(project: str, goal: str) -> dict:
    try:
        from core import project_snapshot
        targets = project_snapshot.relevant_files(project, goal)
        imp = project_snapshot.impact(project, targets, goal)
        return {"targets": targets, "risk": (imp.get("risk") or {}).get("value"),
                "direct": len((imp.get("direct") or {}).get("value") or []),
                "tests": len((imp.get("tests") or {}).get("value") or []),
                "status": imp.get("status")}
    except Exception:  # noqa: BLE001
        return {"status": "unavailable"}


def create(project: str, goal: str, agent: str = "builder",
           reasoning: str | None = None) -> dict:
    """Register ONE productization request. Real gates BEFORE any model
    turn: existing project, supported type, dangerous-request gate — the
    same security machinery the orchestrator uses."""
    goal = str(goal or "").strip()
    if not goal:
        raise PipelineError(400, "درخواست خالی است")
    d = paths.project_dir(project)                    # validates the name
    ptype = ""
    try:
        ptype = str((json.loads((d / "meta.json").read_text(
            encoding="utf-8")) or {}).get("type") or "")
    except (OSError, ValueError):
        ptype = ""
    if ptype not in SUPPORTED_TYPES:
        raise PipelineError(400,
            "خط لوله برای نوع «%s» پشتیبانی نمی‌شود" % (ptype or "?"))
    rc = requestcheck.analyze(goal)
    if not rc["allowed"]:
        raise PipelineError(400, "درخواست خطرناک رد شد: " + rc["note"])
    pid = "pl-%s-%s" % (datetime.now().strftime("%H%M%S"),
                        secrets.token_hex(2))
    rec = {
        "id": pid, "project": project, "goal": goal[:4000],
        "agent": str(agent or "builder"), "type": ptype,
        "state": "queued", "stage": "plan", "done": {},
        "plan_text": None, "fix_cycles": 0, "turns": 0,
        "project_model": _model_summary(project),
        "meta": {"reasoning": reasoning} if reasoning else {},
        "code_impact": _code_impact(project, goal),
        "error": None, "result": None, "task_id": None,
        "history": [], "created": _now(), "updated": _now(),
    }
    try:
        task = tasksmod.add_task(project, goal, agent=agent,
                                 max_attempts=6, kind="pipeline",
                                 meta={"pipeline_id": pid,
                                       "reasoning": reasoning})
    except tasksmod.TaskError as exc:
        raise PipelineError(exc.status, exc.message)
    rec["task_id"] = task["id"]
    _ev(rec, "خط لوله ایجاد شد (گیت امنیتی درخواست: عبور)")
    _persist(rec)
    from core import factory
    factory.append_history(project, "pipeline-created",
                           "id=%s goal=%s" % (pid, goal[:120]))
    log.info("pipeline %s created for %s (%s)", pid, project, ptype)
    return _view(rec)


# ------------------------------------------------------------------ gates -----

def approve(pid: str, ok: bool) -> dict:
    """The REAL plan gate: True resumes the pipeline (clears the task
    hold); False rejects it terminally — no generation happens."""
    with _lock:
        recs = _load()
        rec = _get(recs, pid)
        if not rec:
            raise PipelineError(404, "خط لوله یافت نشد: " + pid)
        if rec["state"] != "awaiting_approval":
            raise PipelineError(409,
                "خط لوله در گیت تأیید نیست (وضعیت: %s)" % rec["state"])
        tid = rec.get("task_id")
        if ok:
            # Clear the hold BEFORE publishing approved state; a failure keeps
            # the pipeline awaiting so approve() can be retried safely.
            if tid:
                tasksmod.set_hold(tid, None)
            rec["state"] = "approved"
            rec["stage"] = "generate"
            _ev(rec, "برنامه توسط کاربر تأیید شد — تولید آغاز می‌شود")
            _save(recs)
        else:
            rec["state"] = "rejected"
            rec["error"] = "برنامه توسط کاربر رد شد — تولید انجام نشد"
            _ev(rec, "برنامه رد شد — خط لوله به‌صورت نهایی بسته شد")
            if tid:
                tasksmod.cancel_task(tid)
            _save(recs)
            from core import factory
            factory.append_history(rec["project"], "pipeline-rejected", pid)
    return _view(get(pid))


# ---------------------------------------------------------------- execution ---

def execute(task: dict, worker_id: str) -> None:
    """Called by the orchestrator worker for kind="pipeline" tasks.
    Defensive: an unexpected crash is recorded honestly on BOTH stores."""
    tid = task["id"]
    pid = ((task.get("meta") or {}).get("pipeline_id")) or ""
    try:
        p = get(pid)
    except PipelineError:
        tasksmod.fail_task(tid, "رکورد خط لوله برای این وظیفه یافت نشد",
                           retry=False)
        return
    if p["state"] in ("delivered", "rejected"):
        tasksmod.fail_task(tid, "خط لوله در وضعیت پایانی «%s» است — "
                           "اجرای تکراری ممنوع" % p["state"], retry=False)
        return
    if p["state"] == "failed":
        # AF-35 — an explicit task retry is a trusted resume signal: keep
        # `done`/`stage` and continue instead of a permanent dead end.
        task = tasksmod.get_task(tid)
        if (task.get("meta") or {}).get("resume_failed"):
            p = _persist(dict(p, state="running", error=None))
            _ev(p, " resume صریح خط لولهٔ شکستخورده — مراحل سبز تکرار نمیشوند")
        else:
            tasksmod.fail_task(tid, "خط لوله قبلاً شکست خورد: %s"
                               % (p.get("error") or "")[:220], retry=False)
            return
    if p["state"] == "awaiting_approval":
        # safety net: never burn a claim while the gate is closed
        tasksmod.set_hold(tid, "approval")
        tasksmod.release_task(tid, "در انتظار تأیید برنامه (نگه‌داشته)")
        return
    p = _persist(dict(p, state="running", updated=_now()))
    _ev(p, "ادامهٔ اجرا در مرحلهٔ «%s»" % p["stage"])
    _persist(p)
    try:
        _run_stages(p, tid)
    except PipelineError as exc:
        # AF-20 fix (proven by pl-163855-5ce6): the outer p is STALE —
        # _run_stages persists every intermediate step; re-fetch the
        # latest record so the failure keeps the real stage/turns/done
        # trail and the install evidence instead of overwriting it.
        try:
            p = get(pid)
        except PipelineError:
            pass
        _fail(p, tid, exc.message, exc.result)
    except Exception as exc:  # noqa: BLE001 — honest crash record
        log.exception("pipeline %s crashed", pid)
        try:
            p = get(pid)
        except PipelineError:
            pass
        _fail(p, tid, "خطای داخلی در مرحلهٔ «%s»: %s" % (p["stage"], exc))


def _fail(p: dict, tid: str, message: str, result: dict | None = None) -> None:
    p = dict(p, state="failed", error=str(message)[:600],
             result=result, updated=_now())
    _ev(p, "شکست صادقانه: " + message)
    _persist(p)
    try:
        tasksmod.fail_task(tid, message, retry=False, result=result)
    except tasksmod.TaskError:
        pass
    try:
        from core import factory
        factory.append_history(p["project"], "pipeline-failed",
                               "%s: %s" % (p["id"], message[:150]))
    except Exception:  # noqa: BLE001
        pass
    log.warning("pipeline %s FAILED: %s", p["id"], message[:200])


def _set(p: dict, **kw) -> dict:
    p = dict(p, **kw, updated=_now())
    return _persist(p)


def _done(p: dict, stage: str, summary: dict) -> dict:
    done = dict(p.get("done") or {})
    done[stage] = dict(summary, at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    _ev(p, "مرحلهٔ «%s» سبز شد" % stage)
    return _set(p, done=done)


def _run_stages(p: dict, tid: str) -> None:
    from core import factory
    project, agent = p["project"], (p.get("agent") or "").strip()

    # ── 1) PLAN (real model turn) → HUMAN GATE ────────────────────────────
    if "plan" not in p["done"]:
        resp = factory.plan(project, p["goal"], agent=agent or None)
        p = _set(p, turns=int(p.get("turns") or 0) + 1)
        if not factory.wait_done(resp["session_id"], WAIT_S,
                                 since_message_id=resp.get("message_id"),
                                 headless=True, project=project,
                                 task_id=str(p.get("task_id") or "")):
            raise PipelineError(500, "برنامه‌ریزی مدل به مهلت رسید یا ناموفق بود")
        plan_text = factory.plan_text(project) or ""
        if not plan_text.strip():
            raise PipelineError(500, "برنامهٔ خالی از مدل — ادامهٔ شفاف ناممکن")
        p = _set(p, plan_text=plan_text[:6000])
        p = _done(p, "plan", {"session": resp["session_id"][-12:],
                              "plan_chars": len(plan_text)})
        # AF-26 / C3 — order matters: publish the hold BEFORE the awaiting
        # state.  The old order let approve() clear a hold that did not exist
        # yet, after which the worker installed it permanently → the task
        # stayed held forever with no way to unhold it.  With this order any
        # approve() that can see "awaiting_approval" also sees the hold and
        # clears it; an earlier approve simply gets 409 and can be retried.
        try:
            tasksmod.set_hold(tid, "approval")
        except tasksmod.TaskError as exc:
            raise PipelineError(500, "توقف روی گیت ناموفق: %s" % exc.message)
        p = _set(p, state="awaiting_approval")
        _ev(p, "گیت تأیید برنامه: خط لوله متوقف شد (تأیید انسانی لازم)")
        _persist(p)
        try:
            tasksmod.release_task(tid, "در انتظار تأیید برنامه")
        except tasksmod.TaskError as exc:
            raise PipelineError(500, "توقف روی گیت ناموفق: %s" % exc.message)
        return

    # ── 2) GENERATE (real model turn; plan is the approved input) ─────────
    if "generate" not in p["done"]:
        resp = factory.generate(project, p["goal"],
                                include_plan=p.get("plan_text"),
                                agent=agent or None,
                                reasoning=((p.get("meta") or {}).get("reasoning")
                                           or None))
        p = _set(p, turns=int(p.get("turns") or 0) + 1, stage="generate")
        if not factory.wait_done(resp["session_id"], WAIT_S,
                                 since_message_id=resp.get("message_id"),
                                 headless=True, project=project,
                                 task_id=str(p.get("task_id") or "")):
            raise PipelineError(500, "تولید مدل به مهلت رسید یا ناموفق بود")
        p = _done(p, "generate", {"session": resp["session_id"][-12:]})

    # ── AF-22: android rides its OWN stages — there is no server to boot ──
    if p["type"] == "android":
        _run_stages_android(p, tid)
        return

    # ── 2.5) DEPS (AF-19): declared ≠ installed ≠ usable — survey first,
    # real install only if something declared is missing, re-survey after,
    # and an honest 422 (package + reason) when install can't be verified.
    if "deps" not in p["done"]:
        p = _set(p, stage="deps")
        from core import deps as depsmod, runner as runnermod
        sv = depsmod.survey(project)
        declared = list(sv.get("requirements_declared") or [])
        missing = list(sv.get("missing_requirements") or [])
        # declared-but-unused: keep going, but say it honestly
        third = {t.replace("-", "_").lower()
                 for t in (sv.get("python_imports_third_party") or [])}
        unused = [x for x in declared
                  if depsmod.split_req(x)[0].replace("-", "_").lower()
                  .split("_")[0] not in third]
        if missing:
            ins = runnermod.install_deps(project)
            if not ins.get("verified"):
                raise PipelineError(
                    422, "وابستگی‌های اعلام‌شده نصب/تأیید نشدند — ادامه ممنوع: "
                    + ((ins.get("note") or "")[:240] or "دلیل نامشخص"),
                    result={"install": {k: ins.get(k) for k in
                             ("installed", "note", "failed_packages",
                              "offline", "unverified", "output")},
                            "missing": missing})
            sv = depsmod.survey(project)
            still = list(sv.get("missing_requirements") or [])
            if still:
                raise PipelineError(
                    422, "پس از نصب، این وابستگی‌ها هنوز واردشدنی نیستند: "
                    + ", ".join(still)[:240],
                    result={"missing_after_install": still})
            p = _done(p, "deps", {"installed": declared,
                                  "missing_before": missing})
        elif declared:
            p = _done(p, "deps", {"installed": declared,
                                  "declared_unused": unused})
        else:
            p = _done(p, "deps", {"note": "وابستگی خارجی اعلام نشده"})

    # ── 3) TEST (+ fix via the EXISTING autofix path, ≤ FIX_MAX) ─────────
    # resume: a completed test stage is never re-run (no pointless repeat)
    cycles = int(p.get("fix_cycles") or 0)
    if "test" not in p["done"]:
        p = _set(p, stage="test")
        while True:
            rep = factory.testrun_combined(project)
            if rep.get("ran") and rep.get("ok"):
                p = _done(p, "test", {"cycles": cycles})
                break
            if not rep.get("ran"):
                raise PipelineError(422, "بدون پوشش تست — انتشار ممنوع: %s"
                                    % (rep.get("note") or "")[:200],
                                    result={"testrun": rep})
            if cycles >= FIX_MAX:
                raise PipelineError(422,
                    "آزمون پس از %d دور اصلاح سرخ ماند — توقف صادقانه" % FIX_MAX,
                    result={"testrun": rep, "cycles": cycles})
            cycles += 1
            p = _set(p, stage="fix", fix_cycles=cycles,
                     turns=int(p.get("turns") or 0) + 1)
            _ev(p, "دور اصلاح %d — سپردن به autofix موجود" % cycles)
            # AF-32 — progress-based loop decision; REPLAN strategy reaches
            # the model, ASK/STOP are honest terminal states.
            strategy = None
            try:
                from core import agent_loop
                dv = agent_loop.decide(
                    project, str(p.get("task_id") or ""), p.get("goal") or "",
                    rep, elapsed_s=time.time() - float(p.get("created") or time.time()),
                    attempts=cycles, step="verify", max_attempts=FIX_MAX)
                if dv.get("action") == "REPLAN":
                    strategy = dv.get("strategy")
                if dv.get("action") in ("STOP", "ASK") and not dv.get("success"):
                    raise PipelineError(422,
                        dv.get("question") or dv.get("reason") or
                        "توقف صادقانه توسط حلقه عامل",
                        result={"testrun": rep, "loop": dv, "cycles": cycles})
            except PipelineError:
                raise
            except Exception as exc:  # noqa: BLE001 — never silently degrade
                _ev(p, "loop-degraded: %s" % exc)
                raise PipelineError(422, "تصمیم حلقهٔ تست ناموفق بود: %s" % exc,
                                    result={"testrun": rep})
            from core import autofix
            fx = autofix.run(project, agent=agent or None, strategy=strategy)
            if fx.get("rolled_back") or fx.get("fix", {}).get("model_ok") is False:
                rb_ok = (fx.get("rollback") or {}).get("ok")
                raise PipelineError(422,
                    ("rollback %s (دور %d) — %s" %
                     ("ناموفق؛ کارخانه در وضعیت ناپایدار است" if rb_ok is False
                      else "انجام شد", cycles,
                      (fx.get("note") or "")[:160])),
                    result={"autofix_note": (fx.get("note") or "")[:220],
                            "rollback_ok": rb_ok})

    # ── 4) QUALITY GATE — AF-35: bounded loop, evidence-only, own budget ────
    from core import quality as qualitymod
    if "quality" not in p["done"]:
        p = _set(p, stage="quality")
        q = qualitymod.evaluate(project, boot=True)
        quality_cycles = 0
        while q.get("status") != "PASS":
            status = str(q.get("status") or "")
            if status != "FAIL":
                raise PipelineError(422,
                    "کیفیت قابل اثبات نیست (%s) — انتشار ممنوع" % status,
                    result={"quality": q})
            if quality_cycles >= FIX_MAX:
                raise PipelineError(422, "کیفیت پس از سقف اصلاح FAIL است — "
                                    "نیازمند بازبینی انسانی", result={"quality": q})
            quality_cycles += 1
            p = _set(p, stage="fix", fix_cycles=int(p.get("fix_cycles") or 0) + 1,
                     turns=int(p.get("turns") or 0) + 1)
            strategy = None
            try:
                from core import agent_loop
                qrep = {"ran": bool((q.get("tests") or {}).get("ran")),
                        "ok": False, "note": str(q.get("errors") or [])[:300]}
                dv = agent_loop.decide(
                    project, str(p.get("task_id") or ""), p.get("goal") or "",
                    qrep, elapsed_s=time.time() - float(p.get("created") or time.time()),
                    attempts=quality_cycles, step="quality", progress=True,
                    max_attempts=FIX_MAX)
                if dv.get("action") in ("STOP", "ASK") and not dv.get("success"):
                    raise PipelineError(422,
                        dv.get("question") or dv.get("reason") or
                        "توقف صادقانه در مرحلهٔ کیفیت",
                        result={"quality": q, "loop": dv})
                if dv.get("action") == "REPLAN":
                    strategy = dv.get("strategy")
            except PipelineError:
                raise
            except Exception as exc:  # noqa: BLE001 — never silently skip
                raise PipelineError(422,
                    "تصمیم حلقهٔ کیفیت ناموفق بود: %s" % exc,
                    result={"quality": q})
            from core import autofix
            fx = autofix.run(project, agent=agent or None, strategy=strategy)
            if fx.get("rolled_back") or fx.get("fix", {}).get("model_ok") is False:
                rb_ok = (fx.get("rollback") or {}).get("ok")
                raise PipelineError(422, "اصلاح کیفیت rollback %s — نیازمند بازبینی انسانی"
                                    % ("ناموفق" if rb_ok is False else "انجام شد"),
                                    result={"autofix_note": (fx.get("note") or "")[:220],
                                            "rollback_ok": rb_ok})
            rep = factory.testrun_combined(project)
            if not (rep.get("ran") and rep.get("ok")):
                raise PipelineError(422, "پس از اصلاح کیفیت، آزمون سبز نشد",
                                    result={"testrun": rep})
            q = qualitymod.evaluate(project, boot=True)
        p = _done(p, "quality", {"status": q.get("status"),
                                 "warnings": len(q.get("warnings") or []),
                                 "cycles": quality_cycles})

    # ── 5) RELEASE (existing pipeline; structural verify inside) ──────────
    from core import release as releasemod
    if "release" not in p["done"]:
        p = _set(p, stage="release")
        rel = releasemod.build_release(project)
        if not rel.get("ok"):
            raise PipelineError(422, "انتشار ناموفق: %s"
                                % "; ".join((rel.get("verify") or {})
                                            .get("problems") or [])[:220]
                                or "ساخت بسته ناموفق", result={"release": rel})
        p = _done(p, "release", {"name": rel["name"], "files": rel["files"],
                                 "version": rel["version"],
                                 "sha256": (rel.get("sha256") or "")[:16],
                                 "_path": rel["path"]})
    else:
        # resume: reuse the already-built release, but VERIFY its integrity
        # against the recorded SHA before any delivery.
        done_rel = p["done"]["release"]
        rel = {"path": done_rel.get("_path") or "",
               "files": done_rel.get("files"),
               "version": done_rel.get("version"),
               "sha256": done_rel.get("sha256")}
        from core import action_gate as _ag
        expected = done_rel.get("sha256") or ""
        if expected:
            g = _ag.gate_artifact(rel["path"], expected)
            if g["status"] != "PASS":
                raise PipelineError(422, "بستهٔrelease قبلی با هش ثبتشده مطابقت ندارد",
                                    result={"verification": g})

    # ── 6) DELIVER — independent validation OUTSIDE the factory ───────────
    p = _set(p, stage="deliver")
    deliv = _deliver_validate(p, rel)
    result = {
        "pipeline": p["id"], "project": project, "type": p["type"],
        "zip": rel["path"], "sha256": rel.get("sha256"),
        "version": rel.get("version"), "files": rel.get("files"),
        "delivery_dir": deliv["dir"], "probe": deliv["probe"],
        "turns_used": p.get("turns"), "fix_cycles": cycles,
    }
    p = _done(p, "deliver", {"dir": deliv["dir"],
                             "probe_ok": deliv["probe"].get("ok")})
    p = _set(p, state="delivered", result=result, error=None)
    _ev(p, "تحویل مستقل تأیید شد — %s" % deliv["dir"])
    try:
        tasksmod.finish_task(tid, result=result)
    except tasksmod.TaskError:
        pass
    factory.append_history(project, "pipeline-delivered",
                           "%s → %s (turns=%s)" % (p["id"],
                                                   deliv["dir"], result["turns_used"]))
    log.info("pipeline %s DELIVERED: %s", p["id"], deliv["dir"])


# ═══════════════════════════ AF-22: android stages ═══════════════════════════
# There is no server to boot for an APK — the stages below carry the android
# type through the SAME pipeline record/gates (plan → human approval →
# generate) and then swap the boot-based stages for the REAL artifact stages:
#   deps(note) → build(unsigned APK — aapt2/javac/d8, the compile IS the test)
#   → test(structural+badging+classes.dex) → sign(product key; pass via 0600
#   file:) → verify(independent re-check) → release(AF-12 standard dir,
#   centrally registered) → deliver(independent re-validation).

def _android_logger(slog):
    def log(step, message, status="ok"):
        slog.add(step, message, status)
    return log


def _build_rep_from_done(done_build: dict) -> dict:
    """Reconstruct the build report from a completed build stage (resume)."""
    out = done_build.get("output_dir") or ""
    apk = Path(out) / (done_build.get("unsigned_apk") or "app-unsigned.apk")
    return {"package": done_build.get("package"),
            "version_name": done_build.get("version_name"),
            "version_code": done_build.get("version_code"),
            "signed": False, "output_dir": out,
            "artifacts": {"apk": {"path": str(apk)}},
            "duration_s": done_build.get("duration_s") or 0}


def _run_stages_android(p: dict, tid: str) -> None:
    from core import factory
    from core.engines import android as andmod
    from core.engines.registry import EngineError
    project = p["project"]
    slog = andmod.StepLog()
    alog = _android_logger(slog)
    eng = andmod.AndroidEngine()
    out_dir = (p["done"].get("build") or {}).get("output_dir") or ""

    # ── deps: an android minimal layout declares no python deps ────────────
    if "deps" not in p["done"]:
        p = _set(p, stage="deps")
        p = _done(p, "deps", {"note": "اندروید: وابستگی پایتون ندارد — ساخت "
                                      "با ابزارهای SDK (aapt2/javac/d8/apksigner)"})

    # ── build: unsigned APK — the REAL compile test (javac/d8/aapt2) ───────
    if "build" not in p["done"]:
        p = _set(p, stage="build")
        _ev(p, "ساخت APK بدون امضا — موتور اندروید (کامپایل واقعی = آزمون ساخت)")
        try:
            build_rep = factory.android_build_repair(
                project,
                lambda: eng.build(project, {"kind": "apk", "sign": False}, alog),
                agent=p.get("agent"))
        except EngineError as exc:
            raise PipelineError(exc.status, "ساخت اندروید ناموفق: " + exc.message,
                                result={"steps": slog.steps[-40:]})
        art = (build_rep.get("artifacts") or {}).get("apk") or {}
        out_dir = build_rep.get("output_dir") or out_dir
        p = _done(p, "build", {
            "package": build_rep.get("package"),
            "version_name": build_rep.get("version_name"),
            "version_code": build_rep.get("version_code"),
            "unsigned_apk": Path(art.get("path", "")).name,
            "apk_sha256": (art.get("sha256") or "")[:16],
            "output_dir": out_dir,
            "duration_s": build_rep.get("duration_s")})
        try:
            factory.append_history(project, "pipeline-android-build",
                                   "%s v%s-vc%s" % (p["id"], build_rep.get("version_name"),
                                                    build_rep.get("version_code")))
        except Exception:
            pass
    build_rep = _build_rep_from_done(p["done"]["build"])

    # ── test: the artifact is the test subject (no server to boot) ─────────
    if "test" not in p["done"]:
        p = _set(p, stage="test")
        try:
            eng.verify_artifacts(Path(build_rep["output_dir"]), alog)
        except EngineError as exc:
            raise PipelineError(exc.status,
                                "آزمون بستهٔ اندروید ناموفق: " + exc.message,
                                result={"steps": slog.steps[-40:]})
        p = _done(p, "test", {"kind": "artifact",
                              "note": "MANIFEST + badging + classes.dex — بستهٔ واقعی سالم است"})

    # ── sign: product key only; the passphrase travels via a 0600 file ─────
    if "sign" not in p["done"]:
        p = _set(p, stage="sign")
        _ev(p, "امضای APK با کلید اختصاصی محصول (رمز فقط از فایل 0600)")
        try:
            srep = eng.sign_and_finalize(project, build_rep, alog)
        except EngineError as exc:
            raise PipelineError(exc.status, "امضای اندروید ناموفق: " + exc.message,
                                result={"steps": slog.steps[-40:]})
        p = _done(p, "sign", {"sign_verified": True,
                              "apk": Path(srep["apk"]).name,
                              "sha256": srep["sha256"][:16]})
        out_dir = srep.get("output_dir") or out_dir

    # ── verify: INDEPENDENT re-check (fresh apksigner/aapt2/MANIFEST) ──────
    if "verify" not in p["done"]:
        p = _set(p, stage="verify")
        try:
            vrep = eng.verify_artifacts(
                Path(out_dir), alog,
                expect={"package": build_rep.get("package"),
                        "version_code": build_rep.get("version_code"),
                        "version_name": build_rep.get("version_name")})
        except EngineError as exc:
            raise PipelineError(exc.status,
                                "راستی‌آزمایی اندروید ناموفق: " + exc.message,
                                result={"steps": slog.steps[-40:]})
        p = _done(p, "verify", {"sign_verified": True, "badging": vrep["badging"]})

    # ── release: the engine already wrote the AF-12 standard (release.json +
    # MANIFEST) and registered it centrally — the stage records it honestly ──
    if "release" not in p["done"]:
        p = _set(p, stage="release")
        dmeta = json.loads((Path(out_dir) / "release.json").read_text(encoding="utf-8"))
        p = _done(p, "release", {"name": dmeta.get("name"),
                                 "version": dmeta.get("version"),
                                 "version_code": dmeta.get("version_code"),
                                 "files": sum(1 for f in Path(out_dir).iterdir() if f.is_file()),
                                 "sha256": ((dmeta.get("artifacts") or {}).get("apk") or {})
                                           .get("sha256", "")[:16],
                                 "_path": str(out_dir)})

    # ── deliver: independent validation OUTSIDE the factory stages ────────
    p = _set(p, stage="deliver")
    deliv = _deliver_validate_android(out_dir)
    dmeta = json.loads((Path(out_dir) / "release.json").read_text(encoding="utf-8"))
    result = {
        "pipeline": p["id"], "project": project, "type": p["type"],
        "kind": "apk", "apk": ((dmeta.get("artifacts") or {}).get("apk") or {}).get("path"),
        "sha256": ((dmeta.get("artifacts") or {}).get("apk") or {}).get("sha256"),
        "version": dmeta.get("version"), "version_code": dmeta.get("version_code"),
        "package": dmeta.get("package"), "signed": dmeta.get("signed"),
        "delivery_dir": deliv["dir"], "probe": deliv["probe"],
        "turns_used": p.get("turns"), "fix_cycles": int(p.get("fix_cycles") or 0),
    }
    p = _done(p, "deliver", {"dir": deliv["dir"], "probe_ok": True})
    p = _set(p, state="delivered", result=result, error=None)
    _ev(p, "تحویل مستقل تأیید شد — %s" % deliv["dir"])
    try:
        tasksmod.finish_task(tid, result=result)
    except tasksmod.TaskError:
        pass
    factory.append_history(project, "pipeline-delivered",
                           "%s → %s (turns=%s)" % (p["id"], deliv["dir"],
                                                   p.get("turns")))
    log.info("pipeline %s DELIVERED (android): %s", p["id"], deliv["dir"])


def _deliver_validate_android(out_dir) -> dict:
    """AF-22 — independent validation OUTSIDE the factory stages: a fresh
    apksigner verify + aapt2 badging + MANIFEST.sha256 re-check against the
    delivered dir (an APK has no server to boot — the probe IS signature
    verification). Unverified signature = honest refusal to deliver."""
    from core.engines import android as andmod
    from core.engines.registry import EngineError
    slog = andmod.StepLog()
    eng = andmod.AndroidEngine()
    try:
        v = eng.verify_artifacts(Path(out_dir), slog.add)
    except EngineError as exc:
        raise PipelineError(exc.status,
                            "تحویل ناموفق — راستی‌آزمایی مستقل: " + exc.message,
                            result={"steps": slog.steps[-20:]})
    dmeta = json.loads((Path(out_dir) / "release.json").read_text(encoding="utf-8"))
    if not dmeta.get("signed") or not dmeta.get("sign_verified"):
        raise PipelineError(422, "بستهٔ تحویل امضاشده نیست — تحویل ممنوع")
    return {"dir": str(out_dir),
            "probe": {"ok": True,
                      "method": "apksigner-verify + MANIFEST.sha256 + badging",
                      "sign_verified": True, "badging": v["badging"]}}


def _deliver_validate(p: dict, rel: dict) -> dict:
    """The one NEW capability: prove the bundle runs WITHOUT the factory —
    extract to deliveries/, boot via its own run.sh, probe health, stop.
    (Same evidence pattern proven manually in AF-17 A4 — now automated.)"""
    ptype = p.get("type") or ""
    if ptype in ("api", "ai-tool"):
        probe_path, expect_json = "/api/health", True
    elif ptype.startswith("fullstack"):
        probe_path, expect_json = "/api/csrf", True
    else:                                   # static-web + node-server
        probe_path, expect_json = "/", False

    dest_root = config.EXPORTS_DIR / "deliveries" / p["id"]
    shutil.rmtree(dest_root, ignore_errors=True)
    dest_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(rel["path"]) as zf:
        if zf.testzip() is not None:
            raise PipelineError(422, "بستهٔ انتشار خراب است (testzip)")
        zf.extractall(dest_root)
    pdir = dest_root / p["project"]
    if not (pdir / "run.sh").is_file():
        raise PipelineError(422, "بستهٔ تحویل run.sh ندارد — تحویل نامعتبر")

    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    finally:
        s.close()
    bootlog = dest_root / "boot.log"
    import urllib.request, urllib.error
    with open(bootlog, "wb") as bl:
        from core import boundary
        proc = subprocess.Popen(["sh", "run.sh"], cwd=str(pdir),
                                env=boundary.clean_env({"PORT": str(port),
                                                        "AI_FACTORY_PORT": str(port)}),
                                stdout=bl, stderr=subprocess.STDOUT)
        try:
            base = "http://127.0.0.1:%d" % port
            deadline = time.time() + 15.0
            probe = {"ok": False, "path": probe_path}
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen(base + probe_path,
                                                timeout=2.0) as r:
                        body = r.read(4096).decode("utf-8", "replace")
                        if r.status == 200:
                            if expect_json:
                                try:
                                    probe["ok"] = json.loads(body).get("ok") is True
                                except ValueError:
                                    probe["ok"] = False
                            else:
                                probe["ok"] = bool(body.strip())
                            probe["http"] = r.status
                            break
                except (urllib.error.URLError, OSError, ValueError):
                    pass
                time.sleep(0.6)
            if not probe["ok"]:
                tail = ""
                try:
                    tail = bootlog.read_text(encoding="utf-8",
                                             errors="replace")[-300:]
                except OSError:
                    pass
                raise PipelineError(422,
                    "بستهٔ تحویل خارج از کارخانه سالم اجرا نشد "
                    "(پروب %s)" % probe_path,
                    result={"probe": probe, "boot_log_tail": tail})
            return {"dir": str(pdir), "probe": probe}
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
