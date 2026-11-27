"""OpenCode bridge — real connection between AI Factory and OpenCode v2.2.6.

Talks to the local OpenCode HTTP server (`opencode serve`, /api/* routes).

Design:
  * the serve process is spawned with fixed argv (shell=False), bound to
    127.0.0.1, and protected with a Basic-auth password kept ONLY in this
    backend (logs/opencode.token, mode 0600). The browser never sees it.
  * config env vars OPENCODE_SESSION_ID / OPENCODE / OPENCODE_TERMINAL /
    AI_AGENT are stripped from the child environment so the bridge talks to
    its own instance, not to the terminal's interactive one.
"""

import base64
import json
import logging
import os
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import core.config as config
import core.memory as memory

log = logging.getLogger("ai-factory.opencode")

# AF-29 — the event types that carry observable tool/permission/outcome facts.
# Other session.* events (text streaming, usage, …) are forwarded to the UI but
# are NOT decision evidence and are therefore not stored in the DecisionLog.
# Verified against a LIVE OpenCode 2.2.6 stream (AF-30): the real names are
# dotted sub-events (session.tool.called / session.tool.success /
# permission.asked ...), NOT the guesses used in AF-29.
OBSERVED_EVENTS = frozenset({
    "session.tool.input.started", "session.tool.input.ended",
    "session.tool.called", "session.tool.success", "session.tool.error",
    "session.tool.failed", "session.tool.progress",
    "session.error", "session.cancelled",
    "session.interrupted", "session.idle", "session.execution.failed",
    "permission.asked", "permission.replied",
})

_lock = threading.Lock()
_serve_log_fp = None


class OcError(Exception):
    """Raised for any OpenCode-side or network error (message is UI-safe)."""

    def __init__(self, status: int, message: str, tag: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message
        self.tag = tag


# ------------------------------------------------------------- auth token ---

def load_token() -> str:
    """Read (or create) the backend-only server password."""
    path = config.OC_TOKEN_FILE
    try:
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(24)
    path.write_text(token, encoding="utf-8")
    os.chmod(path, 0o600)
    os.chmod(config.LOGS_DIR, 0o700)
    return token


def _auth_header(token: str) -> str:
    raw = ("opencode:" + token).encode(encoding="utf-8")
    return "Basic " + base64.b64encode(raw).decode(encoding="ascii")


# ------------------------------------------------------------ process mgmt ---

def _write_pid(pid: int) -> None:
    config.OC_PID_FILE.write_text(str(pid), encoding="ascii")


def _cmdline(pid: int):
    from pathlib import Path

    path = Path(f"/proc/{pid}/cmdline")
    try:
        return path.read_bytes()
    except OSError:
        return None


def is_running() -> bool:
    """Check whether our bridge instance answers on its port."""
    try:
        _request("GET", "/api/info", timeout=2.0)
        return True
    except OcError:
        return False


def stop() -> bool:
    """Kill the serve process spawned by this bridge (verified cmdline)."""
    try:
        pid = int(config.OC_PID_FILE.read_text(encoding="ascii"))
    except (OSError, ValueError):
        return False
    cmd = _cmdline(pid)
    if not cmd or b"opencode" not in cmd or b"serve" not in cmd:
        config.OC_PID_FILE.unlink(missing_ok=True)
        return False
    try:
        os.kill(pid, 15)
        for _ in range(10):
            if _cmdline(pid) is None:
                break
            time.sleep(0.5)
        if _cmdline(pid) is not None:
            os.kill(pid, 9)
        log.info("opencode serve stopped (pid=%s)", pid)
    except OSError as exc:
        log.error("failed to stop opencode serve: %s", exc)
        return False
    finally:
        config.OC_PID_FILE.unlink(missing_ok=True)
    return True


def ensure_running() -> bool:
    """Spawn `opencode serve` if it is not answering; return True when up."""
    with _lock:
        if is_running():
            return True
        token = load_token()
        log_path = config.OC_SERVE_LOG
        # keep a module-level handle so it is never GC-closed in the parent
        global _serve_log_fp
        _serve_log_fp = open(log_path, "ab")
        try:
            env = {k: v for k, v in os.environ.items()
                   if k not in ("OPENCODE_SESSION_ID", "OPENCODE", "OPENCODE_TERMINAL", "AI_AGENT")}
            env["OPENCODE_SERVER_PASSWORD"] = token
            proc = subprocess.Popen(
                ["/data/data/com.termux/files/usr/bin/opencode", "serve",
                 "--hostname", config.OC_HOST, "--port", str(config.OC_PORT),
                 "--log-level", "error"],
                cwd=str(config.PROJECT_ROOT),
                env=env,
                stdout=_serve_log_fp,
                stderr=_serve_log_fp,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            _write_pid(proc.pid)
            log.info("opencode serve spawned (pid=%s port=%s)", proc.pid, config.OC_PORT)
        except OSError as exc:
            raise OcError(500, f"راه‌اندازی سرور OpenCode ناموفق بود: {exc}")
        # wait for health
        deadline = time.time() + 60
        while time.time() < deadline:
            if proc.poll() is not None:
                if self_port_busy():
                    raise OcError(409, "پورت 4556 توسط یک سرور OpenCode دیگر اشغال است. آن را متوقف کنید یا پیکربندی OC_PORT را تغییر دهید.")
                raise OcError(500, "سرور OpenCode بلافاصله متوقف شد — لاگ: logs/opencode-serve.log")
            try:
                _request("GET", "/api/info", timeout=2.0, token=token)
                return True
            except OcError:
                time.sleep(1.0)
        raise OcError(503, "سرور OpenCode در ۶۰ ثانیه آماده نشد")


def self_port_busy() -> bool:
    import socket as _s
    sock = _s.socket()
    try:
        return sock.connect_ex((config.OC_HOST, config.OC_PORT)) == 0
    finally:
        sock.close()


# ----------------------------------------------------------------- server ---


def _request(method: str, path: str, params: dict | None = None,
             body: dict | None = None, timeout: float = 30.0,
             token: str | None = None, raw: bool = False):
    token = token or load_token()
    url = config.OC_BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", _auth_header(token))
    if data is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            if not payload.strip():
                return None  # e.g. 204 No Content
            if raw:
                return resp.status, json.loads(payload.decode("utf-8"))
            return json.loads(payload.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            err = json.loads(exc.read().decode("utf-8"))
            tag = err.get("_tag", "")
            message = err.get("message") or ""
        except Exception:
            tag, message = "", ""
        raise OcError(exc.code, message or f"HTTP {exc.code}", tag)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise OcError(502, f"اتصال به سرور OpenCode برقرار نشد: {exc.reason if hasattr(exc, 'reason') else exc}")


def info() -> dict:
    ensure_running()
    return _request("GET", "/api/info")


def models() -> list[dict]:
    ensure_running()
    data = _request("GET", "/api/model")["data"]
    out = []
    for m in data:
        out.append({
            "providerID": m.get("providerID"),
            "modelID": m.get("modelID"),
            "name": m.get("name"),
            "context": (m.get("limit") or {}).get("context"),
            "output": (m.get("limit") or {}).get("output"),
            "active": m.get("status") == "active",
            "enabled": m.get("enabled") is True,
            "variants": [{"id": (v or {}).get("id"),
                          "settings": (v or {}).get("settings") or {}}
                         for v in (m.get("variants") or [])],
        })
    allowed = [m for m in out if m["enabled"] and m["active"]]
    allowed.sort(key=lambda m: m["name"] or "")
    return allowed


def _project_dir(project: str) -> str:
    """Strict validation: only directories inside projects/ are allowed."""
    from core.paths import project_dir
    return str(project_dir(project).resolve())


def sessions(project: str) -> list[dict]:
    ensure_running()
    directory = _project_dir(project)
    data = _request("GET", "/api/session", params={"limit": "200"})["data"]
    out = []
    for s in data:
        loc = (s.get("location") or {}).get("directory")
        if loc == directory:
            out.append({
                "id": s.get("id"),
                "title": s.get("title") or "(بدون عنوان)",
                "created": s.get("time", {}).get("created"),
                "updated": s.get("time", {}).get("updated"),
                "model": (s.get("model") or {}).get("id"),
                "agent": s.get("agent"),
            })
    out.sort(key=lambda s: s.get("updated") or 0, reverse=True)
    return out


def create_session(project: str, title: str | None = None) -> dict:
    ensure_running()
    directory = _project_dir(project)
    # AF-29 — the SAME permission policy applies to chat and headless runs.
    # Only creates a missing policy; an existing user policy is never touched.
    try:
        from core import boundary
        policy = boundary.ensure_project_policy(project)
        if policy.get("status") in ("failed", "incomplete"):
            log.warning("project policy not safe for %s: %s",
                        project, policy.get("note") or policy.get("status"))
    except Exception:  # noqa: BLE001 — never block a session on policy setup
        policy = {"status": "unavailable"}
        log.warning("project policy setup failed for %s", project, exc_info=True)
    body = {"location": {"directory": directory}}
    if title:
        body["title"] = title[:120]
    resp = _request("POST", "/api/session", body=body, timeout=30)
    s = resp["data"]
    log.info("opencode session created: %s for %s", s.get("id"), project)
    return {"id": s.get("id"), "title": s.get("title") or "(بدون عنوان)",
            "directory": (s.get("location") or {}).get("directory"),
            "policy_status": policy.get("status")}


def _session_location(session_id: str) -> str | None:
    info = _request("GET", f"/api/session/{urllib.parse.quote(session_id)}", timeout=15)
    return ((info.get("data") or {}).get("location") or {}).get("directory")


def _classify_provider_failure(etype: str, message: str) -> int:
    hay = f"{etype} {message}".lower()
    if any(w in hay for w in ("auth", "api key", "apikey", "unauthorized", "permission")):
        return 401
    if any(w in hay for w in ("credit", "insufficient", "billing", "payment")):
        return 402
    if any(w in hay for w in ("quota", "rate", "limit", "429", "exceeded")):
        return 429
    if any(w in hay for w in ("model", "unknown", "not found", "invalid")):
        return 404
    if "timeout" in hay or "timed out" in hay:
        return 504
    return 502


def test_model(project: str, provider_id: str, model_id: str,
               timeout_s: int = 60) -> dict:
    """AF-23 (part 4): low-cost provider connection test with HONEST
    classification. Sends a tiny prompt ("فقط عدد ۱ را برگردان") to a
    dedicated throwaway session (deleted afterwards), then classifies the
    real outcome: ok / network / auth / quota / model / timeout."""
    started = time.time()
    ensure_running()
    _project_dir(project)                      # validates the project name/path
    models()                                   # cheap list; membership checked below
    available = {m.get("modelID") or m.get("id")
                 for m in models() if (m.get("providerID") == provider_id
                                       and m.get("enabled", True))}
    if model_id not in available:
        raise OcError(404, f"مدل «{model_id}» در فهرست فعال Provider «{provider_id}» موجود نیست")
    sess = create_session(project, "تست اتصال Provider")
    sid = sess["id"]
    try:
        prompt(project, sid, "تست اتصال. فقط عدد ۱ را برگردان.",
               model=f"{provider_id}/{model_id}")
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            st = state(sid)
            if st.get("running"):
                time.sleep(1.5)
                continue
            if st.get("outcome") == "succeeded":
                # AF-23 (proven real): OpenCode can answer with a DIFFERENT
                # model than requested (it used to silently ignore overrides)
                # — the test must verify the model that ACTUALLY answered.
                answered = None
                try:
                    h = history(sid)
                    a = [m for m in (h.get("messages") or [])
                         if m.get("type") == "assistant"]
                    if a:
                        answered = ((a[0].get("model") or {}).get("id")
                                    if isinstance(a[0].get("model"), dict)
                                    else a[0].get("model"))
                except OcError:
                    pass
                if answered and model_id not in str(answered):
                    raise OcError(409,
                                  f"مدل «{model_id}» اعمال نشد — Provider با مدل "
                                  f"«{answered}» پاسخ داد")
                return {"ok": True,
                        "reason": "Provider پاسخ می‌دهد و آماده استفاده است",
                        "elapsed_ms": int((time.time() - started) * 1000),
                        "model": f"{provider_id}/{model_id}",
                        "answered_by": answered or ""}
            le = st.get("last_error") or {}
            msg = le.get("message") or ""
            etype = (le.get("type") or "").lower()
            raise OcError(_classify_provider_failure(etype, msg),
                          msg or "Provider پاسخ موفقی نداد", tag=etype)
        raise OcError(504, "Provider در زمان تعیین‌شده پاسخ نداد")
    finally:
        delete_session(sid)


def set_session_model(session_id: str, provider_id: str, model_id: str,
                      variant: str | None = None) -> None:
    # AF-23 (proven real): OpenCode v2.2.6 requires this exact shape
    # {"model": {"id": ..., "providerID": ...}} — and the per-turn
    # "model" key in the PROMPT body is silently ignored (a turn requested
    # with glm answered with the session's deepseek). So switching models
    # happens HERE: the session model is the single source of truth.
    body = {"id": model_id, "providerID": provider_id}
    if variant:
        body["variant"] = variant
    _request("POST", f"/api/session/{urllib.parse.quote(session_id)}/model",
             body={"model": body}, timeout=15)


def prompt(project: str, session_id: str, text: str, model: str | None = None,
           agent: str | None = None, variant: str | None = None) -> dict:
    """Send a message to a session. Returns the queued user message.

    `model` is an optional "providerID/modelID" override (verified: OpenCode
    accepts {"model": {"providerID": ..., "modelID": ...}} in the prompt body
    and answers with that exact model).

    AF-06: `agent` scopes the injected memory context. A labeled block built by
    memory.build_context (project/agent/global, secret-filtered, size-capped)
    is prepended to the user text. Empty memory ⇒ no injection; retrieval
    errors are logged, never raised.
    """
    ensure_running()
    directory = _project_dir(project)
    sess_loc = _session_location(session_id)
    if sess_loc != directory:
        raise OcError(403, "نشست انتخاب‌شده به این پروژه تعلق ندارد")
    if not (text or "").strip():
        raise OcError(400, "پیام خالی است")
    if len(text) > 20000:
        raise OcError(400, "پیام بیش از حد بلند است (حداکثر 20000 کاراکتر)")
    payload_text = text
    try:
        ctx = memory.build_context(project, agent)
    except Exception:  # never fatal — context is an enhancement only
        log.warning("memory context unavailable; sending without it", exc_info=True)
        ctx = ""
    if ctx:
        combined = ctx + "\n\n" + text
        if len(combined) > 20000:
            # keep the user's text intact; shrink the context block to fit
            budget = 20000 - len(text) - 2
            if budget > 0:
                cut = ctx[:budget]
                nl = cut.rfind("\n")
                ctx = cut[:nl] if nl > 0 else cut
            else:
                ctx = ""
            combined = ctx + "\n\n" + text if ctx else text
        payload_text = combined
    # AF-35 — factory-generated prompts already carry perception (snapshot +
    # observations + memory). Injecting them again here floods/duplicates.
    if "### وضعیت واقعی پروژه" not in text:
        # AF-39 — chat decisions use the SAME structured Project Model, with
        # only the chat-relevant slice of facts.
        try:
            from core import project_snapshot
            model_txt = project_snapshot.to_prompt(
                project_snapshot.build(project), focus="chat")
            if model_txt:
                payload_text = model_txt + "\n\n" + payload_text
            # AF-40 — only evidence-relevant code with real line numbers.
            code_txt = project_snapshot.select_context(project, text)
            if code_txt:
                payload_text = code_txt + "\n\n" + payload_text
        except Exception:  # noqa: BLE001 — optional
            log.warning("project model unavailable for %s", project, exc_info=True)
        try:
            from core import agent_events
            observed = agent_events.decision_prompt(project, session_id)
            if observed:
                payload_text = observed + "\n\n" + payload_text
        except Exception:  # noqa: BLE001 — perception never blocks a model turn
            log.warning("tool observations unavailable for %s", session_id, exc_info=True)
        try:
            from core import agent_memory
            mem = agent_memory.recall(project, text, session_id=session_id)
            if mem:
                payload_text = mem + "\n\n" + payload_text
        except Exception:  # noqa: BLE001 — optional
            log.warning("agent memory unavailable for %s", session_id, exc_info=True)
    # Single final budget pass AFTER all blocks are assembled. The user text
    # is always preserved; optional context is trimmed from the front.
    if len(payload_text) > 20000:
        room = 20000 - len(text) - 2
        optional = payload_text[:len(payload_text) - len(text) - 2] if \
            payload_text.endswith(text) else ""
        payload_text = (optional[:room] + "\n\n" + text) if room > 0 and optional \
            else text
    body: dict = {"text": payload_text}
    if model and "/" in model:
        prov, _, mid = model.partition("/")
        if prov and mid:
            # AF-23 (proven real): OpenCode v2.2.6 IGNORES a per-turn "model"
            # in the prompt body — the session model is what actually runs.
            # So the requested model is set on the session first (the UI
            # selection then truly applies to this and subsequent turns).
            try:
                set_session_model(session_id, prov, mid, variant=variant)
            except OcError as exc:
                raise OcError(exc.status,
                              f"اعمال مدل «{model}» روی نشست انجام نشد: {exc.message}",
                              tag=exc.tag)
    resp = _request("POST", f"/api/session/{urllib.parse.quote(session_id)}/prompt",
                    body=body, timeout=30)
    msg = resp["data"]
    return {"message_id": msg.get("id"), "session_id": session_id}


def _map_message(m: dict) -> dict:
    t = m.get("type")
    if t == "user":
        return {"type": "user", "id": m.get("id"), "text": m.get("text", ""),
                "created": (m.get("time") or {}).get("created")}
    if t == "assistant":
        return {"type": "assistant", "id": m.get("id"),
                "model": (m.get("model") or {}).get("id"),
                "finish": m.get("finish"),
                "outcome": m.get("outcome"),   # AF-29: real turn result, not a guess
                "error": m.get("error"),
                "tokens": m.get("tokens"),
                "parts": [{"type": p.get("type"), "text": p.get("text", "")
                           } for p in (m.get("content") or [])],
                "completed": (m.get("time") or {}).get("completed"),
                "created": (m.get("time") or {}).get("created")}
    if t == "idle":
        return {"type": "idle", "outcome": m.get("outcome"), "id": m.get("id"),
                "error": m.get("error"),
                "created": (m.get("time") or {}).get("created"),}
    return None


def history(session_id: str) -> dict:
    ensure_running()
    sess_loc = _session_location(session_id)
    if not sess_loc:
        raise OcError(404, "نشست یافت نشد")
    resp = _request("GET", f"/api/session/{urllib.parse.quote(session_id)}/message", timeout=30)
    items = []
    for m in resp.get("data", []):
        mapped = _map_message(m)
        if mapped:
            items.append(mapped)
    return {"session": session_id, "directory": sess_loc, "messages": items}


def state(session_id: str) -> dict:
    """Single source of truth for a session's REAL live state (additive).

    Returns outcome of the last finished turn, busy/idle wire times, and the
    most recent model error — so the UI never has to guess.
    """
    ensure_running()
    sess_loc = _session_location(session_id)
    if not sess_loc:
        raise OcError(404, "نشست یافت نشد")
    sid = urllib.parse.quote(session_id)
    resp = _request("GET", f"/api/session/{sid}", timeout=20)
    s = resp.get("data", {})
    t = s.get("time", {}) or {}
    busy_ts = t.get("busy") or 0
    idle_ts = t.get("idle") or 0
    running = busy_ts > 0 and busy_ts >= idle_ts
    last_error = None
    pending: list[dict] = []
    try:
        msgs = _request("GET", f"/api/session/{sid}/message", timeout=20).get("data", [])
        newest = None
        for m in msgs:                     # newest-first in practice
            if newest is None:
                newest = m
            err = m.get("error")
            if err and isinstance(err, dict) and last_error is None:
                last_error = {
                    "type": err.get("type"),
                    "message": err.get("message"),
                    "retry_after_ms": err.get("retryAfterMs"),
                }
        # AF-56 — busy/idle alone can lie after an abort: a stale busy stamp
        # stays newer than idle. A TERMINAL assistant message with a real
        # completion time settles the turn honestly.
        if newest and (newest.get("type") == "assistant" and
                       (newest.get("time") or {}).get("completed") and
                       str(newest.get("finish") or "").lower() in
                       ("stop", "error", "aborted", "interrupted")):
            running = False
    except OcError:
        pass
    try:
        pending = permissions(session_id)
    except OcError:
        pass
    return {
        "session": session_id,
        "outcome": s.get("outcome"),          # succeeded/failed/interrupted/None
        "running": running,                   # terminal message wins over stale busy
        "busy": busy_ts, "idle": idle_ts,
        "updated": t.get("updated"),
        "created": t.get("created"),
        "model": s.get("model"),
        "directory": sess_loc,
        "last_error": last_error,
        "pending_permissions": [str((p or {}).get("id") or "") for p in pending],
        "awaiting_permission": bool(pending),
    }


def interrupt(session_id: str) -> bool:
    ensure_running()
    resp = _request("POST", f"/api/session/{urllib.parse.quote(session_id)}/interrupt", timeout=15)
    return bool(resp.get("interrupted"))


def delete_session(session_id: str) -> None:
    sess_loc = _session_location(session_id)
    if not (sess_loc and "/projects/" in sess_loc):
        raise OcError(403, "حذف نشست مجاز نیست")
    _request("DELETE", f"/api/session/{urllib.parse.quote(session_id)}", timeout=15)


def record_tool_events(project: str, session_id: str,
                       task_id: str = "") -> int:
    """AF-36 — record REAL tool call/result events from the backend.

    Independent of any browser SSE connection. Uses the same agent_events
    store/schema as the SSE path; deterministic ids prevent duplicates.
    """
    try:
        from core import agent_events
        resp = _request(
            "GET", f"/api/session/{urllib.parse.quote(session_id)}/message",
            timeout=20)
        messages = resp.get("data") or []
    except Exception as exc:  # noqa: BLE001 — recording never blocks the engine
        log.warning("tool event recording failed for %s: %s", session_id, exc)
        return 0
    count = 0
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("type") != "assistant":
            continue
        agent_hint = str(msg.get("agent") or "")
        for part in (msg.get("content") or []):
            if not isinstance(part, dict) or part.get("type") != "tool":
                continue
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            raw_status = str(state.get("status") or "running").lower()
            meta = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
            exit_code = str(meta.get("exit") or "")
            if raw_status in ("error", "failed") or (
                    raw_status == "completed" and exit_code not in ("", "0", "None")):
                etype = "session.tool.failed"
            elif raw_status == "completed":
                etype = "session.tool.success"
            else:
                etype = "session.tool.called"
            evt = {
                "id": "tool-%s-%s" % (part.get("id") or "", raw_status),
                "type": etype,
                "data": {
                    "sessionID": session_id,
                    "id": part.get("id") or "",
                    "name": part.get("name") or "",
                    "agent": agent_hint,
                    "input": state.get("input"),
                    "content": state.get("content"),
                    "error": state.get("error"),
                    "metadata": meta,
                },
            }
            try:
                agent_events.record(evt, project, session_id, task_id)
                count += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("tool event store failed: %s", exc)
    return count


def permissions(session_id: str) -> list[dict]:
    ensure_running()
    resp = _request("GET", f"/api/session/{urllib.parse.quote(session_id)}/permission", timeout=15)
    out = []
    for p in resp.get("data", []):
        out.append({
            "id": p.get("id"),
            "action": p.get("action"),
            "resource": p.get("resource"),
            "resources": p.get("resources"),
            "agent": p.get("agent"),
            "metadata": p.get("metadata"),
        })
    return out


def permission_reply(session_id: str, request_id: str, decision: str,
                     message: str | None = None) -> None:
    """Reply to a permission request.

    AF-44 — OpenCode v2.2.6 accepts an OPTIONAL ``message`` on the reply.
    Verified live: ``reject`` WITHOUT a message fails the tool with
    "The user declined this tool call" and interrupts the assistant step;
    ``reject`` WITH a message returns a structured ``permission.rejected``
    tool error carrying that text, and the SAME session continues.  The
    security decision is unchanged — only the feedback to the model differs.
    """
    ensure_running()
    if decision not in ("once", "always", "reject"):
        raise OcError(400, "تصمیم نامعتبر است (once/always/reject)")
    body = {"decision": decision}
    if message:
        body["message"] = str(message)[:2000]
    _request("POST",
             f"/api/session/{urllib.parse.quote(session_id)}/permission/{urllib.parse.quote(request_id)}/reply",
             body=body, timeout=15)


# AF-44 — one shared headless permission handler for factory.wait_done and
# orchestrator._wait_idle.  Both previously replied "reject" silently, which
# made OpenCode abort the turn.  This keeps a bounded per-session record of
# requests already answered so a slow poll cannot reply twice.
_headless_replied_lock = threading.Lock()
_headless_replied: dict[str, set[str]] = {}


def reply_headless(project: str, session_id: str, task_id: str = "") -> dict:
    """Decide every pending headless permission and record REAL evidence.

    Returns honest counts (never a success flag): decisions, rejections and
    rejection signatures.  A rejected command is recorded as BLOCKED and its
    structured explanation is returned to the model in the same session.
    """
    from core import agent_events, boundary
    out: dict = {"decisions": {}, "rejections": 0,
                 "rejection_signatures": []}
    try:
        pending = permissions(session_id)
    except OcError as exc:
        log.warning("permission list failed for %s: %s", session_id, exc)
        return out
    with _headless_replied_lock:
        seen = _headless_replied.setdefault(session_id, set())
        if len(seen) > 200:
            seen.clear()
    for req in pending:
        rid = req.get("id") or req.get("requestID")
        if not rid:
            continue
        with _headless_replied_lock:
            if rid in seen:
                continue
            seen.add(rid)
        resources = req.get("resources") or [req.get("resource")]
        decision = boundary.headless_permission(req.get("action"), resources,
                                                project)
        message = None
        explanation = None
        if decision == "reject":
            explanation = boundary.explain_rejection(req.get("action"),
                                                     resources, project)
            message = explanation["feedback"]
        try:
            permission_reply(session_id, rid, decision, message=message)
        except OcError as exc:
            log.warning("permission reply failed (%s): %s", rid, exc)
            with _headless_replied_lock:
                seen.discard(rid)          # allow a genuine retry
            continue
        agent_events.record_permission_decision(
            session_id, rid, decision, actor="headless-policy", request=req)
        out["decisions"][decision] = out["decisions"].get(decision, 0) + 1
        if decision == "reject":
            agent_events.record_policy_rejection(
                session_id, rid, project=project, task_id=task_id,
                action=str(req.get("action") or ""),
                resource=resources, explanation=explanation or {})
            out["rejections"] += 1
            out["rejection_signatures"].append(
                str((explanation or {}).get("signature") or rid))
    return out


def is_quota_error(text: str) -> bool:
    """AF-47 — recognise a REAL provider quota/rate-limit failure."""
    import re as _re
    return bool(_re.search(
        r"(?i)(usage limit|rate.?limit|quota|retry_after|free usage|"
        r"too many requests|limit reached|insufficient credits)",
        str(text or "")))


# AF-56 — one-shot permission decisions. Both the UI reply and the controlled
# expiry path must go through this guard so a late click can never produce a
# second, contradictory reply to the same request.
_reply_once_lock = threading.Lock()
_replied_once: dict[str, set[str]] = {}


def reply_once(session_id: str, request_id: str, decision: str,
               message: str | None = None) -> bool:
    """Reply ONLY if this request is still pending and not already answered."""
    key = str(session_id)
    with _reply_once_lock:
        done = _replied_once.setdefault(key, set())
        if request_id in done:
            return False
        pending = {str((p or {}).get("id") or (p or {}).get("requestID") or "")
                   for p in permissions(session_id)}
        if request_id not in pending:
            done.add(request_id)
            return False
        done.add(request_id)
    permission_reply(session_id, request_id, decision, message=message)
    return True


def expire_permission(session_id: str, request_id: str) -> bool:
    """AF-56 — official OpenCode reply path for an unanswered request.

    A `reject` WITH a message returns a structured tool error to the model
    (verified in AF-44) — never an auto-approval and never a fake success.
    """
    return reply_once(session_id, request_id, "reject",
                      "درخواست مجوز بدون پاسخ منقضی شد")


# AF-56 — controlled expiry of UNANSWERED permissions. Requests that were
# already pending when the watcher started are marked legacy and are NEVER
# auto-rejected: an existing stuck session must keep waiting for the user.
_watch_lock = threading.Lock()
_watch_started = False
_legacy_requests: set[str] = set()
_first_seen: dict[str, float] = {}


def all_sessions() -> list[str]:
    ensure_running()
    resp = _request("GET", "/api/session", timeout=15)
    out = []
    for s in resp.get("data", []):
        sid = str((s or {}).get("id") or "")
        if sid.startswith("ses_"):
            out.append(sid)
    return out


def start_permission_watch(ttl_s: float | None = None) -> None:
    """Start the one background watcher (idempotent)."""
    global _watch_started
    with _watch_lock:
        if _watch_started:
            return
        _watch_started = True
    try:
        ttl = float(ttl_s if ttl_s is not None else
                    os.environ.get("AF_PERMISSION_TTL_S", "300"))
    except (TypeError, ValueError):
        ttl = 300.0

    # AF-56 — legacy snapshot MUST wait until OpenCode is actually reachable.
    # Snapshotting before it is up would auto-expire an old stuck request.
    def snapshot_legacy() -> bool:
        try:
            sessions = all_sessions()
        except OcError:
            return False
        if not sessions:
            return False
        for sid in sessions:
            try:
                for p in permissions(sid):
                    rid = str((p or {}).get("id") or "")
                    if rid:
                        _legacy_requests.add(rid)
            except OcError:
                continue
        return True

    def loop() -> None:
        ready = False
        while True:
            time.sleep(5.0)
            if not ready:
                ready = snapshot_legacy()
                if not ready:
                    continue
            for sid in all_sessions():
                try:
                    pending = permissions(sid)
                except OcError:
                    continue
                now = time.time()
                for p in pending:
                    rid = str((p or {}).get("id") or "")
                    if not rid or rid in _legacy_requests:
                        continue
                    with _reply_once_lock:
                        first = _first_seen.setdefault(rid, now)
                        answered = rid in _replied_once.get(sid, set())
                    if answered or now - first < ttl:
                        continue
                    try:
                        if expire_permission(sid, rid):
                            log.info("permission %s expired after %.0fs", rid, ttl)
                    except OcError as exc:
                        log.warning("permission expiry failed (%s): %s", rid, exc)

    threading.Thread(target=loop, daemon=True, name="af-permission-watch").start()


def permission_event_data(evt: dict) -> dict | None:
    """Return a flat permission-request payload if the event carries one."""
    if "permission" not in str(evt.get("type", "")):
        return None
    if str(evt.get("type", "")).endswith(".replied"):
        return None                      # a reply is not a new request
    d = evt.get("data") or {}
    req = d.get("request") or d
    rid = req.get("id") or req.get("requestID")
    if not rid:
        return None
    # AF-29 — keep the FULL request so the user can see exactly what will
    # happen.  The old relay dropped agent/resources/metadata/diff.
    return {
        "id": rid,
        "sessionID": req.get("sessionID") or d.get("sessionID"),
        "action": req.get("action"),
        "resource": req.get("resource"),
        "resources": req.get("resources"),
        "agent": req.get("agent"),
        "metadata": req.get("metadata") or {
            k: req[k] for k in ("save", "source") if k in req},
        "diff": req.get("diff") or d.get("diff"),
        "title": req.get("title") or d.get("title"),
    }


def status() -> dict:
    running = is_running()
    version = None
    pid = None
    if running:
        try:
            info = _request("GET", "/api/info", timeout=3)
            version = info.get("version")
            pid = info.get("pid")
        except OcError:
            pass
    return {"running": running, "version": version, "pid": pid,
            "base_url": config.OC_BASE_URL}
