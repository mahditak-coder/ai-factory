"""P7-3 — Orchestrator: drives queued tasks with real agents (concurrency
starts at 1; P7-4 raises it to 2 behind a resource guard).

Lifecycle (always EXPLICIT — the orchestrator never auto-starts):
    POST /api/orchestrator/start {workers: 1|2}
    POST /api/orchestrator/stop
    GET  /api/orchestrator

Each worker loop:
  1. resource guard — refuse to claim while MemAvailable is under the floor
     (mobile-first; honest status reports the wait instead of thrashing);
  2. atomically claim the oldest queued task (core.tasks.claim_next);
  3. per-project lock — tasks for a busy project are released back untouched;
  4. execute: requestcheck gate (P6-3) → factory.generate (agent
     instructions + model override) → wait for the model →
     factory.testrun_combined (EXACT P6-1 gate) → bounded multi-cycle loop
     (observe → evaluate → replan → fix → verify) → finish/fail with evidence.

Only the ONE shared OpenCode server is used (agent = one OC session).
"""

import hashlib
import json
import logging
import threading
import time

from core.model_gateway import OcError, gateway


def oc_state(session_id: str) -> dict:
    """Stable Core seam → ModelGateway.state (real OpenCode adapter)."""
    return gateway().state(session_id)
from core import paths, tasks, factory

log = logging.getLogger("ai-factory.orchestrator")

MIN_AVAIL_MB = 450          # never claim below this MemAvailable
TASK_TIMEOUT_S = 480        # model wait ceiling per turn
FIX_WAIT_S = 300            # model wait ceiling for the fix round
POLICY_ASK_LIMIT = 3        # real policy rejections before parking on ASK

_reg_lock = threading.RLock()   # P7-3 fix: reentrant — start()/status() nest
_stop_event = threading.Event()
_threads: list[threading.Thread] = []
_worker_ids: list[str] = []
_started_at: float | None = None

_project_locks: dict[str, threading.Lock] = {}
_project_locks_guard = threading.Lock()


def _mem_available_mb() -> float:
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return -1.0            # unknown → do not block, but report honestly


def _project_lock(project: str) -> threading.Lock:
    with _project_locks_guard:
        if project not in _project_locks:
            _project_locks[project] = threading.Lock()
        return _project_locks[project]


def running() -> bool:
    with _reg_lock:
        return bool(_threads) and any(t.is_alive() for t in _threads)


def status() -> dict:
    with _reg_lock:
        workers = [{"id": wid, "alive": t.is_alive()}
                   for wid, t in zip(_worker_ids, _threads)]
    mem = _mem_available_mb()
    return {"running": running(), "workers": workers,
            "started": _started_at,
            "queue": tasks.stats(),
            "memory_available_mb": round(mem, 1) if mem >= 0 else None,
            "min_required_mb": MIN_AVAIL_MB}


def start(workers: int = 1) -> dict:
    workers = max(1, min(int(workers), 2))          # mobile ceiling: 2
    if running():                                    # fast path, no nesting
        return {"ok": True, "already_running": True, **status()}
    with _reg_lock:
        if running():                                # double-checked
            return {"ok": True, "already_running": True, **status()}
        _stop_event.clear()
        _threads.clear()
        _worker_ids.clear()
        # honest recovery first: orphaned tasks from a previous process
        recovered = tasks.recover_orphans()
        for n in range(1, workers + 1):
            wid = f"w{n}"
            th = threading.Thread(target=_worker, args=(wid,),
                                  daemon=True, name=f"orch-{wid}")
            th.start()
            _threads.append(th)
            _worker_ids.append(wid)
        _started_at = time.time()
        log.info("orchestrator started with %d worker(s), recovered=%d",
                 workers, len(recovered))
        return {"ok": True, "recovered": len(recovered), **status()}


def stop() -> dict:
    with _reg_lock:
        _stop_event.set()
    return {"ok": True, "stopping": True, **status()}


# ------------------------------------------------------------------ worker ---

def _worker(worker_id: str) -> None:
    log.info("worker %s looping", worker_id)
    while not _stop_event.is_set():
        mem = _mem_available_mb()
        if mem >= 0 and mem < MIN_AVAIL_MB:
            log.warning("worker %s: low memory (%.0fMB) — waiting", worker_id, mem)
            _stop_event.wait(5.0)
            continue
        # P7-4: skip BEFORE claiming when the oldest queued task belongs to a
        # project another worker is executing — the task stays queued with
        # attempts untouched, and no claim/release churn happens.
        try:
            nxt = tasks.peek_next_queued()
        except Exception as exc:  # noqa: BLE001
            log.exception("peek failed: %s", exc)
            nxt = None
        if nxt and _project_lock(nxt["project"]).locked():
            _stop_event.wait(2.0)
            continue
        try:
            task = tasks.claim_next(worker_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("claim failed: %s", exc)
            _stop_event.wait(2.0)
            continue
        if not task:
            _stop_event.wait(2.0)
            continue
        lock = _project_lock(task["project"])
        if not lock.acquire(blocking=False):
            # rare race between peek and claim — give back gracefully
            tasks.release_task(task["id"],
                               "پروژه هم‌اکنون توسط کارگر دیگر مشغول است")
            _stop_event.wait(1.0)
            continue
        try:
            _execute(task, worker_id)
        except Exception as exc:  # noqa: BLE001  (worker must never die)
            log.exception("task %s crashed", task["id"])
            try:
                tasks.fail_task(task["id"], f"خطای orchestrator: {exc}")
            except Exception:  # noqa: BLE001
                pass
        finally:
            lock.release()
    log.info("worker %s stopped", worker_id)


def _wait_idle(session_id: str, timeout: float, project: str = "",
               since_message_id: str | None = None,
               task_id: str = "") -> bool:
    """Wait until the model turn of this FRESH session really ends.

    AF-35 — when the caller supplies the queued message id, use the SAME
    evidence-based turn detector as factory.wait_done (fast turns are no
    longer missed by the busy/idle poll). Legacy polling stays as fallback.
    """
    if since_message_id:
        done = factory.wait_done(session_id, timeout,
                                 since_message_id=since_message_id,
                                 headless=True, project=project,
                                 task_id=task_id)
        if not done:
            return False
        try:
            st = oc_state(session_id)
        except OcError:
            return False
        if st.get("last_error"):
            return False
        outcome = str(st.get("outcome") or "").lower()
        return outcome not in ("failed", "aborted", "error", "interrupted")
    deadline = time.time() + timeout
    seen_busy = False
    while time.time() < deadline:
        if _stop_event.is_set():
            return False
        try:
            st = oc_state(session_id)
        except OcError:
            return False
        if st.get("running"):
            seen_busy = True
        elif seen_busy:
            if st.get("last_error"):
                log.warning("session %s model error: %s", session_id,
                            st["last_error"])
                return False
            outcome = str(st.get("outcome") or "").lower()
            return outcome not in ("failed", "aborted", "error", "interrupted")
        elif st.get("last_error"):
            return False            # failed before ever going busy
        # AF-29/AF-44 — headless execution has no browser to answer an "ask"
        # permission. Fail closed, but return the REAL rejection reason to the
        # model in the same session so it can pick a safe alternative.
        try:
            gateway().headless_permissions(project, session_id, task_id)
        except Exception:  # noqa: BLE001 — policy denial is best-effort
            log.warning("headless permission check failed for %s", session_id,
                        exc_info=True)
        time.sleep(3.0)
    return False


def specialist_plan(project: str, goal: str, product_type: str = "") -> list[dict]:
    """AF NEXT P3 — split work by product type and specialist dependencies.

    Additive: the proven generate/pipeline path is unchanged. This exposes the
    real specialist DAG so Workspace/API callers can drive the new team.
    """
    from core import agent_collaboration as collab, specialists as sp
    plan = []
    for group in sp.parallel_groups(product_type):
        for role_id in group:
            ok, why = collab.can_start(project, role_id)
            plan.append({"role": role_id, "parallel_group": group,
                         "can_start": ok, "reason": why,
                         "goal": goal[:400]})
    return plan


def quality_gate(project: str, engine: str = "", do_build: bool = True) -> dict:
    """AF NEXT P5 — additive call into the central Quality Gate/Release Gate."""
    from core import engine_catalog, engine_selection, quality_gate as qg
    if not engine:
        from core import factory, paths
        ptype = (factory.read_meta(paths.project_dir(project))
                 if hasattr(factory, "read_meta") else {}).get("type", "")
        choice = engine_selection.best(ptype or "webapp", "")
        engine = (choice or {}).get("engine") or "web_saas"
    report = qg.evaluate(project, engine, do_build=do_build)
    return {"engine": engine, **qg.gate(report), "report": report}


def _model_failure_reason(session_id: str) -> str:
    """Honest root cause for a failed turn — surfaced into the task record
    (e.g. provider free-limit with retry_after, model errors, timeouts)."""
    try:
        st = oc_state(session_id)
    except OcError:
        return "پاسخ مدل قابل پیگیری نبود (نشست در دسترس نیست)"
    err = st.get("last_error") or {}
    if err:
        msg = str(err.get("message") or err)
        after = err.get("retry_after_ms")
        extra = f" (retry_after_ms={after})" if after else ""
        return f"خطای مدل: {msg[:300]}{extra}"
    return "مدل در سقف زمانی تمام نکرد (بدون خطای مدل ثبت‌شده)"


def _quota_fallback(tid: str, project: str, agent_name: str, sid: str,
                    plan: dict | None, reason: str, cycles: int,
                    context: dict) -> dict | None:
    """AF-47 — handle a REAL quota/rate-limit failure without guessing.

    Returns None when the error is not a quota error. Otherwise either parks
    the SAME task on a transparent ASK (no verified alternative) or continues
    the SAME session with a REAL connected alternative model, once.
    """
    from core import agents as agentsmod
    if not gateway().is_quota_error(reason):
        return None
    alt = agentsmod.alternative_model(agent_name)
    if not alt:
        _park_ask(
            tid, project,
            "سهمیه/محدودیت نرخ مدل تمام شد: " + str(reason)[:220] +
            " — Provider جایگزین تأییدشده و متصلی موجود نیست. "
            "بدون تلاش تکراری: پس از شارژ سهمیه ادامه دهیم یا باreport متوقف شویم؟",
            ["ادامه پس از شارژ سهمیه", "توقف باreport"], "ASK",
            context=context)
        return {"parked": True, "ask": True}
    try:
        fx = factory.fix_request(
            project,
            [{"file": "model", "kind": "quota",
              "message": ("سهمیه مدل تمام شد؛ با provider جایگزین تأییدشدهٔ %s "
                          "در همان نشست و همان Task ادامه کن.") % alt}],
            cycle_no=cycles + 1, agent=agent_name, plan=plan, model=alt)
    except OcError as exc:
        _park_ask(tid, project,
                  "جایگزینی مدل ناموفق بود: %s" % exc.message,
                  ["تلاش مجدد پس از شارژ", "توقف باreport"], "ASK",
                  context=context)
        return {"parked": True, "ask": True}
    ok = _wait_idle(fx["session_id"], FIX_WAIT_S, project,
                    fx.get("message_id"), tid)
    return {"parked": False, "alt": alt, "turn_ok": ok,
            "rep": _combined_testrun(project)}


def _combined_testrun(project: str) -> dict:
    return factory.testrun_combined(project)


def _park_ask(tid: str, project: str, question: str, options: list,
              decision: str, context: dict | None = None) -> str:
    """AF-37 — park a task on ONE validated human answer.

    The token is single-use and bound to this task/ask; the context preserves
    the exact execution point so resume continues the SAME cycle.
    """
    from core import agent_events, confirmations
    cf = confirmations.create(
        project, "agent-ask",
        {"task_id": tid, "question": question, "options": options,
         "decision": decision}, question, "")
    tasks.ask_task(tid, question, options, cf["confirm_token"], decision,
                   context=context or {})
    agent_events.record_permission_decision(
        "", cf["confirm_token"], "ask", actor="agent-policy",
        request={"question": question, "options": options,
                 "project": project, "task_id": tid,
                 "context": context or {}})
    log.info("task %s parked on ASK (%s, stage=%s)", tid, decision,
             (context or {}).get("stage", "start"))
    return cf["confirm_token"]


def _execute(task: dict, worker_id: str) -> None:
    from core import agents as agentsmod, requestcheck
    from core import memory as memorymod
    tid, project = task["id"], task["project"]
    goal = task["goal"]
    agent_name = task["agent"]

    # AF-18 — integrated request→delivery pipeline: delegate to the
    # pipeline state machine (same queue, same worker, same locks).
    if task.get("kind") == "pipeline":
        from core import pipeline
        try:
            pipeline.execute(task, worker_id)
        except Exception:  # noqa: BLE001 — execute is already defensive
            log.exception("pipeline task %s escaped execute()", tid)
            try:
                tasks.fail_task(tid, "خطای داخلی خط لوله — بازبینی لاگ",
                                retry=False)
            except Exception:  # noqa: BLE001
                log.exception("pipeline fail_task also failed")
        return

    t0 = time.time()
    cycles = 1   # AF-52 — must exist before any early failure/quota path
    log.info("worker %s executing task %s (project=%s agent=%s)",
             worker_id, tid, project, agent_name)

    # terminal-failure bookkeeping helper: bump agent perf ONLY when the
    # task really ends failed (requeued attempts are not terminal)
    def _fail(err: str, retry: bool = True, result: dict | None = None):
        t = tasks.fail_task(tid, err, retry=retry, result=result)
        if t["state"] == "failed":
            memorymod.bump_agent(agent_name, ok=False,
                                 duration_s=round(time.time() - t0, 1),
                                 error=err, project=project)
        return t

    try:
        agent = agentsmod.get_agent(agent_name)
    except agentsmod.AgentError as exc:
        _fail(f"عامل نامعتبر: {exc.message}", retry=False)
        return
    if not agent.get("enabled"):
        _fail(f"عامل «{agent_name}» غیرفعال است", retry=False)
        return

    # P8-4: agents may declare allowed project types (schema field existed
    # since P7-1 but was never enforced) — declared scope is now REAL: an
    # out-of-scope task fails fast, without burning a model turn.
    allowed = agent.get("allowed_types")
    if allowed:
        ptype = factory.read_meta(paths.project_dir(project)).get("type", "")
        if ptype not in allowed:
            _fail(f"عامل «{agent_name}» برای نوع «{ptype}» مجاز نیست "
                  f"(محدودهٔ عامل: {', '.join(allowed)})", retry=False)
            return

    # P6-3 gate: dangerous requests never reach the model (no retry).
    rc = requestcheck.analyze(goal)
    if not rc["allowed"]:
        factory.append_history(project, "task-rejected", rc["note"])
        _fail("درخواست خطرناک رد شد: " + rc["note"], retry=False)
        return

    # AF NEXT Phase 02 — every user request receives one canonical, versioned
    # request record before specialist/model work. The adapter is optional and
    # non-breaking: a failure here never stops the proven Phase 01 path, and
    # legacy compat mode skips canonical intake without deleting any record.
    try:
        from core import request_intelligence as ri
        if ri.compat_mode() == "canonical":
            cr = ri.intake(goal, project=project,
                           source_channel="orchestrator",
                           request_id="req-task-" + tid)
            factory.append_history(
                project, "canonical-request",
                "%s %s" % (cr["request_id"], cr["status"]))
    except Exception as exc:  # noqa: BLE001 — compatibility adapter, never fatal
        log.warning("canonical request intake failed for task %s: %s", tid, exc)

    factory.append_history(project, "task-start",
                           f"agent={agent_name} goal={goal[:150]}")
    # AF-31 — record a small, dependency-aware plan for this task. Failure of
    # the optional planner must never stop the proven orchestrator path.
    from core import agent_policy
    plan = None
    decision = {"decision": "PROCEED"}
    try:
        from core import agent_memory, agent_planner, paths
        ptype = str((factory.read_meta(paths.project_dir(project)) or {}).get("type") or "")
        decision = agent_policy.assess(goal, {"type": ptype})
        # AF-35 — build the plan from the REAL project type, never a fabricated
        # "webpage" default (which made the Android branch unreachable).
        from core import project_snapshot
        snap = project_snapshot.build(project)
        plan = agent_planner.create(
            project, goal, snapshot=snap,
            decision=decision, task_id=tid)
        try:
            plan["project_model"] = {
                "status": snap.get("status"),
                "sections": list((snap.get("sections") or {}).keys()),
                "entry": ((snap.get("sections") or {}).get("entry") or {}).get("path"),
                "commands": (snap.get("sections") or {}).get("commands", {}).get("items"),
            }
        except Exception:  # noqa: BLE001
            pass
        # AF-40 — pick relevant files and analyse impact BEFORE any model turn.
        try:
            targets = project_snapshot.relevant_files(project, goal)
            imp = project_snapshot.impact(project, targets, goal)
            plan["code_impact"] = imp
            plan["selected_files"] = targets
            from core import agent_planner as _ap
            _ap._put(plan)
            agent_memory.remember(
                project, "decision",
                "selected=%s risk=%s" % (targets, (imp.get("risk") or {}).get("value")),
                task_id=tid, tags=["code-impact", str((imp.get("risk") or {}).get("value"))])
        except Exception as exc:  # noqa: BLE001 — optional, never block
            log.warning("code impact failed for task %s: %s", tid, exc)
        agent_memory.remember(project, "decision", goal, task_id=tid,
                              tags=[decision["decision"], ptype])
    except Exception as exc:  # noqa: BLE001 — optional intelligence
        log.warning("plan setup failed for task %s: %s", tid, exc)
        plan = None
    # A material ambiguity or a sensitive action must NOT reach the model.
    # AF-36 — park the SAME task on a one-shot confirmation instead of a dead
    # failure; a valid user answer resumes it with plan/memory/decision log.
    task_rec = tasks.get_task(tid)
    ask = task_rec.get("ask") or {}
    answered = bool(task_rec.get("ask_answer")) and \
        ask.get("decision") == decision.get("decision")
    if agent_policy.needs_user(decision) and not answered:
        q = decision.get("question") or "تصمیم انسانی لازم است"
        _park_ask(tid, project, q, decision.get("options", []),
                  decision.get("decision", ""),
                  context={"stage": "start", "decision": decision})
        return
    if agent_policy.needs_user(decision) and answered:
        log.info("task %s resumed after user answer", tid)
        decision = {"decision": "PROCEED"}
    # AF-37 — a mid-loop ASK resumes HERE, not as a fresh generation.
    resuming_loop = False
    resume_ctx = (task_rec.get("ask") or {}).get("context") or {}
    if resume_ctx.get("stage") == "loop" and task_rec.get("ask_answer"):
        try:
            from core import agent_planner as _ap
            sid = str(resume_ctx.get("session_id") or "")
            rep = dict(resume_ctx.get("rep") or {})
            cycles = int(resume_ctx.get("cycles") or 1)
            last_sig = resume_ctx.get("last_sig")
            loop_log = list(resume_ctx.get("loop_log") or [])
            turn_ok = bool(resume_ctx.get("turn_ok"))
            turn_outcome = str(resume_ctx.get("turn_outcome") or "")
            t0 = time.time() - float(resume_ctx.get("elapsed") or 0)
            plan = _ap.get(str(resume_ctx.get("plan_id") or ""))
            log.info("task %s resumed mid-loop at cycle %d", tid, cycles)
            resuming_loop = True
        except Exception as exc:  # noqa: BLE001 — safe fallback to fresh run
            log.warning("mid-loop resume failed for %s: %s", tid, exc)
            resuming_loop = False
    if not resuming_loop:
        # AF-42 — the constraint comes from the REAL environment model, not a
        # hard-coded guess. Only an executable, allowed test command is offered.
        env_sec = ((snap.get("sections") or {}).get("environment") or {}) \
            if 'snap' in locals() else {}
        caps = env_sec.get("capabilities") or {}
        allowed_cmd = (plan or {}).get("verify_command") or ""
        forbidden = [k for k, v in caps.items()
                     if not (v or {}).get("value")]
        note = "[محیط اجرا] "
        if allowed_cmd:
            note += "دستور تست مجاز: %s. " % allowed_cmd
        if forbidden:
            note += "غیرمجاز/ناموجود: %s. " % ",".join(forbidden)
        note += ("سرور، شبکه و فرایند پسزمینه مجاز نیستند؛ فقط داخل پوشهٔ پروژه "
                 "جستجو و ویرایش کن.")
        composed = goal + "\n\n" + note
        if (agent.get("instructions") or "").strip():
            composed = (agent["instructions"].strip()
                        + "\n\nدرخواست کاربر: " + goal)
        model = agentsmod.resolve_model(agent) or None
        try:
            res = factory.generate(project, composed, model=model, agent=agent_name,
                                   plan=plan,
                                   reasoning=((task.get("meta") or {}).get("reasoning")
                                              or None))
        except OcError as exc:
            _fail(f"تولید ناموفق: {exc.message}")
            return
        sid = res["session_id"]

        # AF-35 — completion is evidence-based, but NEVER on user stop, timeout
        # or a still-running turn. The interrupted-turn override is limited to a
        # verified idle session with outcome="interrupted" and green tests.
        turn_ok = _wait_idle(sid, TASK_TIMEOUT_S, project,
                             res.get("message_id"), tid)
        if _stop_event.is_set():
            _fail("توقف صریح حلقه توسط کاربر", retry=False,
                  result={"session_id": sid, "turn_ok": turn_ok})
            return
        rep = _combined_testrun(project)
        turn_outcome = ""
        try:
            st = oc_state(sid)
            turn_outcome = str(st.get("outcome") or "")
            still_running = bool(st.get("running"))
        except OcError:
            still_running = True
        # AF-44 — REAL policy rejections are recorded evidence in this session.
        rej_stats = {"total": 0, "latest": None}
        try:
            from core import agent_events as _ae
            rej_stats = _ae.policy_rejection_stats(project, sid, tid)
            if rej_stats.get("total"):
                log.info("task %s session %s: %d policy rejection(s)",
                         tid, sid, rej_stats["total"])
        except Exception:  # noqa: BLE001 — optional evidence
            pass
        if not turn_ok:
            # Re-verify once the session is actually idle; a live/timed-out turn
            # can never be converted into success by a transient green snapshot.
            rep = _combined_testrun(project)
            allowed = (not still_running and rep.get("ran") and rep.get("ok"))
            # A turn may finish with an error finish code after the real work was
            # already written; honesty is preserved in result.turn_ok/turn_outcome.
            # A user stop or a still-running timeout can never reach this branch.
            if not allowed:
                # AF-44 — after a structured rejection, try ONE bounded recovery
                # in the SAME session with the exact reason and safe alternatives
                # from the Project/Path/Environment models. Never re-run a denied
                # command and never fabricate a green result.
                if (rej_stats.get("total") and
                        rej_stats["total"] < POLICY_ASK_LIMIT):
                    try:
                        latest = rej_stats.get("latest") or {}
                        meta = latest.get("metadata") or {}
                        detail = (
                            "reason=%s; forbidden=%s; scope=%s; limit=%s; "
                            "safe alternatives=%s" % (
                                meta.get("reason") or "policy-rejection",
                                ", ".join(str(x) for x in
                                          (meta.get("forbidden") or []))[:200],
                                str(meta.get("scope") or "")[:160],
                                str(meta.get("policy_limit") or "")[:180],
                                "; ".join(str(x) for x in
                                          (meta.get("alternatives") or []))[:300]))
                        fx = factory.fix_request(
                            project,
                            [{"file": "policy", "kind": "policy-rejection",
                              "message": detail}],
                            cycle_no=2, agent=agent_name,
                            strategy="alternate-tool", plan=plan)
                        fx_ok = _wait_idle(fx["session_id"], FIX_WAIT_S, project,
                                           fx.get("message_id"), tid)
                        rep = _combined_testrun(project)
                        if fx_ok and not (rep.get("ran") and rep.get("ok")):
                            log.warning("policy recovery turn ended without green tests")
                    except Exception as exc:  # noqa: BLE001 — fall through honestly
                        log.warning("policy recovery failed for %s: %s", tid, exc)
                if not (rep.get("ran") and rep.get("ok")):
                    if rej_stats.get("total"):
                        # Precise ASK with the REAL limit — not a generic
                        # "Step interrupted" that hides the policy decision.
                        latest = rej_stats.get("latest") or {}
                        meta = latest.get("metadata") or {}
                        reason = (
                            "Policy رد کرد: %s | ممنوع: %s | محدودیت: %s | "
                            "جایگزین امن: %s" % (
                                meta.get("reason") or "policy-rejection",
                                ", ".join(str(x) for x in
                                          (meta.get("forbidden") or []))[:160],
                                str(meta.get("policy_limit") or "")[:160],
                                "; ".join(str(x) for x in
                                          (meta.get("alternatives") or []))[:200]))
                        _park_ask(
                            tid, project,
                            reason + " — آیا با همان نشست و جایگزین امن ادامه شود؟",
                            ["ادامه ایمن", "توقف باreport"], "ASK",
                            context={"stage": "loop", "session_id": sid,
                                     "plan_id": str((plan or {}).get("id") or ""),
                                     "cycles": 1, "last_sig": None, "rep": rep,
                                     "loop_log": [], "elapsed": time.time() - t0,
                                     "turn_ok": turn_ok,
                                     "turn_outcome": turn_outcome,
                                     "policy_rejections": rej_stats["total"]})
                        return
                    # AF-41 — an aborted tool/turn keeps its session, plan and
                    # evidence; park on ONE human decision instead of losing them.
                    err = (st.get("last_error") or {}) if 'st' in locals() else {}
                    if str(err.get("type") or "") in ("aborted", "interrupted"):
                        _park_ask(
                            tid, project,
                            "اجرای مدل قطع شد (ابزار رد یاinterrupt). اسناد حفظ شدند. "
                            "آیا ادامه با همان نشست ایمن است؟",
                            ["ادامه ایمن", "توقف باreport"], "ASK",
                            context={"stage": "loop", "session_id": sid,
                                     "plan_id": str((plan or {}).get("id") or ""),
                                     "cycles": 1, "last_sig": None, "rep": rep,
                                     "loop_log": [], "elapsed": time.time() - t0,
                                     "turn_ok": turn_ok,
                                     "turn_outcome": turn_outcome})
                        return
                    reason = _model_failure_reason(sid)
                    q = _quota_fallback(
                        tid, project, agent_name, sid, plan, reason, cycles,
                        {"stage": "loop", "session_id": sid,
                         "plan_id": str((plan or {}).get("id") or ""),
                         "cycles": 1, "last_sig": None, "rep": rep,
                         "loop_log": [], "elapsed": time.time() - t0,
                         "turn_ok": turn_ok, "turn_outcome": turn_outcome})
                    if q is not None:
                        if q.get("parked"):
                            return
                        rep = q.get("rep") or rep
                        if not (rep.get("ran") and rep.get("ok")):
                            _fail("ادامه با provider جایگزین بدون شاهد سبز "
                                  "متوقف شد: " + reason[:160], retry=False,
                                  result={"session_id": sid, "testrun": rep,
                                          "model_fallback": q.get("alt")})
                            return
                    else:
                        _fail(reason, retry=False,
                              result={"session_id": sid, "testrun": rep,
                                      "turn_ok": turn_ok,
                                      "turn_outcome": turn_outcome})
                        return
    if plan:
        try:
            from core import agent_planner as _ap
            _ap.mark(plan["id"], "inspect", "PASS", "ProjectSnapshot")
            _ap.mark(plan["id"], "implement", "PASS", sid[-12:])
        except Exception:  # noqa: BLE001 — plan telemetry is optional
            pass
    if not resuming_loop:
        cycles = 1                  # Orchestrator cycles, NOT tool calls
        last_sig: str | None = None
        loop_log: list[dict] = []
    agent_loop_budget = 15 * 60     # independent wall-clock cap (AF-35)

    # AF-34 — bounded Execute → Observe → Evaluate → Replan → Fix → Verify.
    # Continuation is decided by REAL test evidence via agent_loop; the loop
    # is separate from tool calls inside one OpenCode session.
    while not (rep.get("ran") and rep.get("ok")):
        # Secondary local safety cap even if the decision helper degrades.
        if cycles > 8 or time.time() - t0 > agent_loop_budget:
            _fail("سقف ایمنی چرخهٔ Orchestrator رسید بدون شاهد موفقیت",
                  retry=False,
                  result={"session_id": sid, "cycles": cycles,
                          "testrun": rep, "history": loop_log})
            return
        errors = (rep.get("contract") or {}).get("errors") or []
        if not rep.get("ran"):
            break                   # no coverage → honest terminal report
        if not errors:
            # Red own-tests without contract errors: pass the REAL output to
            # the model so it can diagnose the actual first failure.
            own_out = str((rep.get("own_tests") or {}).get("output") or "")
            errors = [{"file": "tests", "kind": "own-test",
                       "message": (own_out[-900:] or
                                   str(rep.get("note") or "acceptance red"))}]

        sig_raw = json.dumps([{"f": e.get("file"), "k": e.get("kind"),
                               "m": str(e.get("message"))[:200]}
                              for e in errors], ensure_ascii=False, sort_keys=True)
        sig = hashlib.sha256(sig_raw.encode("utf-8")).hexdigest()[:16]
        progress = last_sig is None or sig != last_sig
        last_sig = sig

        strategy = None
        dv = {"action": "CONTINUE", "success": False, "reason": "legacy"}
        try:
            from core import agent_loop, agent_planner
            dv = agent_loop.decide(
                project, tid, goal, rep,
                plan_id=str((plan or {}).get("id") or ""),
                elapsed_s=time.time() - t0, attempts=cycles,
                step="verify", progress=progress)
            if dv.get("action") == "ASK" and not dv.get("success"):
                # AF-37 — park on the SAME task with the exact loop state.
                _park_ask(
                    tid, project,
                    dv.get("question") or dv.get("reason") or "تصمیم انسانی لازم است",
                    dv.get("options") or [], "ASK",
                    context={"stage": "loop", "session_id": sid,
                             "plan_id": str((plan or {}).get("id") or ""),
                             "cycles": cycles, "last_sig": last_sig,
                             "rep": rep, "loop_log": loop_log,
                             "elapsed": time.time() - t0,
                             "turn_ok": turn_ok, "turn_outcome": turn_outcome})
                return
            if dv.get("action") == "STOP" and not dv.get("success"):
                _fail(dv.get("reason") or "توقف صادقانه توسط حلقه عامل",
                      retry=False,
                      result={"session_id": sid, "cycles": cycles,
                              "testrun": rep, "loop": dv, "history": loop_log})
                return
            if dv.get("action") == "REPLAN":
                strategy = dv.get("strategy")
                if strategy == "ask-user":
                    _fail("نیاز به تصمیم انسانی: " +
                          str(dv.get("reason") or "مسیر خودکار کافی نیست"),
                          retry=False,
                          result={"session_id": sid, "cycles": cycles,
                                  "loop": dv, "history": loop_log})
                    return
                if plan:
                    agent_planner.replan(plan["id"],
                                         str(errors[0].get("message") or sig),
                                         failed_step="verify",
                                         strategy=strategy)
                agent_planner.mark(plan["id"], "verify", "FAIL",
                                   str(errors[0].get("message") or sig)) if plan else None
                agent_planner.start(plan["id"], "repair") if plan else None
            agent_memory.remember(
                project, "progress" if progress else "error",
                "cycle=%d sig=%s errors=%s" %
                (cycles, sig, ",".join(str(e.get("kind")) for e in errors[:6])),
                task_id=tid, tags=["orchestrator-cycle", str(dv.get("action")),
                                    str(strategy or "")])
        except Exception as exc:  # noqa: BLE001 — fail closed, never blind retry
            _fail("حلقهٔ تصمیم قابل اجرا نبود: %s" % exc, retry=False,
                  result={"session_id": sid, "cycles": cycles,
                          "testrun": rep, "history": loop_log})
            return

        loop_log.append({
            "cycle": cycles, "status": "FAIL", "error_signature": sig,
            "progress": bool(progress), "decision": dv.get("action"),
            "strategy": strategy, "errors": [str(e.get("kind")) for e in errors[:8]],
        })
        log.info("task %s cycle %d red — decision=%s strategy=%s progress=%s",
                 tid, cycles, dv.get("action"), strategy, progress)

        try:
            fx = factory.fix_request(project, errors, cycle_no=cycles + 1,
                                     agent=agent_name, strategy=strategy,
                                     plan=plan)
        except OcError as exc:
            _fail(f"دور اصلاح ناموفق: {exc.message}", retry=False,
                  result={"session_id": sid, "cycles": cycles,
                          "testrun": rep, "history": loop_log})
            return
        fix_ok = _wait_idle(fx["session_id"], FIX_WAIT_S, project,
                            fx.get("message_id"), tid)
        rep = _combined_testrun(project)
        if not fix_ok and not (rep.get("ran") and rep.get("ok")):
            reason = _model_failure_reason(fx["session_id"])
            q = _quota_fallback(
                tid, project, agent_name, sid, plan, reason, cycles,
                {"stage": "loop", "session_id": sid,
                 "plan_id": str((plan or {}).get("id") or ""),
                 "cycles": cycles, "last_sig": last_sig, "rep": rep,
                 "loop_log": loop_log, "elapsed": time.time() - t0,
                 "turn_ok": turn_ok, "turn_outcome": turn_outcome})
            if q is not None:
                if q.get("parked"):
                    return
                rep = q.get("rep") or rep
                if not (rep.get("ran") and rep.get("ok")):
                    _fail("ادامه با provider جایگزین بدون شاهد سبز متوقف شد",
                          retry=False,
                          result={"session_id": sid, "cycles": cycles,
                                  "testrun": rep, "history": loop_log,
                                  "model_fallback": q.get("alt")})
                    return
            else:
                _fail(reason, retry=False,
                      result={"session_id": sid, "cycles": cycles,
                              "testrun": rep, "history": loop_log})
                return
        if _stop_event.is_set():
            _fail("توقف صریح حلقه توسط کاربر", retry=False,
                  result={"session_id": sid, "cycles": cycles,
                          "history": loop_log})
            return
        cycles += 1

    # AF-47 — green tests alone are not delivery-ready. Scan the WHOLE project
    # before finishing; a real secret must be fixed IN THIS PROJECT/SESSION,
    # not merely hidden by blocking the ZIP. Bounded to 3 repair rounds.
    sec_rounds = 0
    sec_gate: dict = {}
    while True:
        try:
            from core import security_gate as _sg
            sec_gate = _sg.scan(project, mode="export")
        except Exception as exc:  # noqa: BLE001 — unscanned product cannot ship
            _fail("دروازهٔ امنیت قابل اجرا نبود؛ تحویل مسدود است: %s" % exc,
                  retry=False,
                  result={"session_id": sid, "cycles": cycles, "testrun": rep,
                          "history": loop_log, "delivery_security": "BLOCKED"})
            return
        if not sec_gate.get("blocked"):
            break
        if sec_rounds >= 3:
            _fail("Secret واقعی در محصول باقی ماند؛ تحویل مسدود است",
                  retry=False,
                  result={"session_id": sid, "cycles": cycles, "testrun": rep,
                          "history": loop_log, "delivery_security": "BLOCKED",
                          "security_findings": sec_gate.get("critical")[:8]})
            return
        guidance = _sg.remediation(sec_gate)
        errors = [{"file": str((f or {}).get("file") or "project"),
                   "kind": str((f or {}).get("kind") or "secret"),
                   "message": guidance}
                  for f in (sec_gate.get("critical") or [])[:3]]
        try:
            fx = factory.fix_request(
                project, errors, cycle_no=cycles + sec_rounds + 1,
                agent=agent_name, strategy="secure-bootstrap", plan=plan)
        except OcError as exc:
            _fail("اصلاح امنیتی ناموفق: %s" % exc.message, retry=False,
                  result={"session_id": sid, "cycles": cycles, "testrun": rep,
                          "history": loop_log, "delivery_security": "BLOCKED"})
            return
        fix_ok = _wait_idle(fx["session_id"], FIX_WAIT_S, project,
                            fx.get("message_id"), tid)
        rep = _combined_testrun(project)
        if not fix_ok and not (rep.get("ran") and rep.get("ok")):
            _fail("اصلاح امنیتی بدون شاهد سبز متوقف شد", retry=False,
                  result={"session_id": sid, "cycles": cycles, "testrun": rep,
                          "history": loop_log, "delivery_security": "BLOCKED"})
            return
        sec_rounds += 1
        loop_log.append({"cycle": cycles + sec_rounds, "status": "SECURITY",
                         "decision": "REPLAN", "strategy": "secure-bootstrap",
                         "errors": [str((f or {}).get("kind") or "secret")
                                    for f in (sec_gate.get("critical") or [])[:8]]})

    result = {"session_id": sid, "agent": agent_name, "cycles": cycles,
              "delivery_security": "PASS",
              "security_scan_files": sec_gate.get("files_scanned", 0),
              "testrun": rep, "history": loop_log,
              "policy_rejections": (rej_stats or {}).get("total", 0),
              "policy_rejection_signatures": (rej_stats or {}).get("signatures", []),
              "code_impact": (plan or {}).get("code_impact"),
              "selected_files": (plan or {}).get("selected_files"),
              "turn_ok": turn_ok, "turn_outcome": turn_outcome,
              "verification": ((rep.get("verification") or {}).get("status") or
                               __import__("core.action_gate", fromlist=["gate_test"])
                               .gate_test(rep)["status"]),
              "duration_s": round(time.time() - t0, 1)}
    if plan:
        try:
            from core import agent_planner as _ap
            if rep.get("ran") and rep.get("ok"):
                _ap.mark(plan["id"], "verify", "PASS", "green testrun")
                _ap.mark(plan["id"], "repair", "SKIP", "not needed")
                _ap.mark(plan["id"], "deliver", "PASS", "task result")
        except Exception:  # noqa: BLE001
            pass
    if tasks.get_task(tid)["state"] == "cancelled":
        _fail("تسک توسط کاربر لغو شد", retry=False, result=result)
        return
    if _stop_event.is_set():
        _fail("توقف صریح حلقه توسط کاربر", retry=False, result=result)
        return
    if rep.get("ran") and rep.get("ok") and \
            result["verification"] == "PASS":
        tasks.finish_task(tid, result)
        memorymod.bump_agent(agent_name, ok=True,
                             duration_s=result["duration_s"],
                             project=project)
        factory.append_history(project, "task-done",
                               f"agent={agent_name} cycles={cycles} "
                               f"duration={result['duration_s']}s")
        log.info("task %s DONE green (cycles=%s)", tid, cycles)
    else:
        note = rep.get("note") or "پذیرش نهایی قرمز بود"
        errs = (rep.get("contract") or {}).get("errors") or []
        _fail(f"پذیرش نهایی قرمز — {note} "
              f"خطاها={[e.get('kind') for e in errs[:5]]}",
              result=result)
        factory.append_history(project, "task-failed",
                               f"cycles={cycles} note={note[:150]}")
        log.warning("task %s FAILED acceptance (cycles=%s)", tid, cycles)
