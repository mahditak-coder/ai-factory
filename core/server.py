"""AI Factory — local management server.

A dependency-free HTTP server (Python stdlib only) that:
  * serves the Persian/RTL dashboard from dashboard/
  * exposes a local-only REST API under /api/
  * logs to logs/server.log and logs/error.log

Security model:
  * listens on 127.0.0.1 only
  * never executes arbitrary user commands (no shell, fixed logic only)
  * every filesystem path is validated to stay inside the project root
  * request bodies are size-limited and JSON-validated
"""

import json
import logging
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

# Ensure the project root is importable whether run as script or module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import (
    AGENTS_DIR, APP_NAME, APP_VERSION, DASHBOARD_DIR, ERROR_LOG_FILE,
    EXPORT_SECRET_NAMES, EXPORTS_DIR, FIX_MAX_ATTEMPTS, LOG_FILE, LOGS_DIR, MAX_BODY_BYTES,
    MODELS_CONFIG, OC_BASE_URL,
    PID_FILE, PROJECT_NAME_RE, PROJECT_ROOT, PROJECTS_DIR, PROVIDERS_DIR, PORT,
    STATIC_EXTENSIONS, TEXT_EXTENSIONS,
)

START_TIME = time.time()
_Name = re.compile(PROJECT_NAME_RE)


def _pview(rec: dict, detail: bool = False) -> dict:
    """ISO-timestamp copy of a pipeline record (never mutates the store)."""
    import time as _t
    v = dict(rec)
    for k in ("created", "updated"):
        ts = rec.get(k)
        v[k] = _t.strftime("%Y-%m-%dT%H:%M:%S") if ts else None
    if not detail and len(str(v.get("plan_text") or "")) > 200:
        v["plan_text"] = str(v["plan_text"])[:200] + "…"
    return v

# P6-2: panel auth — public (no-login) API routes; everything else under
# /api/ requires an authenticated session (+CSRF for mutations).
_PANEL_PUBLIC_GET = {
    "/api/health", "/api/status", "/api/model", "/api/panel/state"}
_PANEL_PUBLIC_POST = {"/api/panel/init", "/api/panel/login"}

# ---------------------------------------------------------------- logging ---

LOGS_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("ai-factory")
log.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

_fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
_fh.setFormatter(_fmt)
log.addHandler(_fh)

_eh = logging.FileHandler(ERROR_LOG_FILE, encoding="utf-8")
_eh.setLevel(logging.ERROR)
_eh.setFormatter(_fmt)
log.addHandler(_eh)

_console = logging.StreamHandler(sys.stderr)
_console.setFormatter(_fmt)
log.addHandler(_console)


# ------------------------------------------------------------- helpers ------

from core import paths as _paths
from core.model_gateway import OcError


def _resolve_safe(base: Path, rel: str) -> Path:
    return _paths.resolve_safe(base, rel)


def _resolve_project_path(project: str, rel: str) -> Path:
    return _paths.project_path(project, rel)


def _is_secret(rel) -> bool:
    """P5: deny-list shared with the exporter (ai_key.txt, ai_admin.json,
    .env) — these can never be read/written/deleted/listed via the panel."""
    name = (str(rel) or "").lower()
    if not name:
        return False
    parts = {p.lower() for p in Path(name).parts}
    if parts & {s.lower() for s in EXPORT_SECRET_NAMES}:
        return True
    return Path(name).name.lower() == ".env" or Path(name).suffix.lower() == ".env"


def _assert_not_secret(path: Path, rel: str) -> None:
    """P5: private files (AI key / admin store / .env) are never readable,
    writable, deletable or listable through the panel — same deny-list the
    exporter uses."""
    if _is_secret(rel):
        raise PermissionError(
            "فایل خصوصی است و از پنل قابل دسترس نیست (کلید/تنظیمات محرمانه)")


def _proc_scan(pattern: bytes) -> bool:
    """Check whether any running process cmdline contains *pattern* (no shell)."""
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            cmdline = (pid_dir / "cmdline").read_bytes()
        except OSError:
            continue
        if pattern in cmdline.replace(b"\x00", b" "):
            return True
    return False


def _mem_info() -> dict:
    info = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, value = line.partition(":")
            parts = value.split()
            if parts:
                info[key.strip()] = int(parts[0])  # kB
    except OSError as exc:
        log.error("meminfo read failed: %s", exc)
    return info


def _fixed_cmd_output(argv: list[str], timeout: float = 5.0) -> str:
    """Run a fixed, whitelisted command list (never user-supplied)."""
    try:
        out = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, shell=False,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("command %s failed: %s", argv[0] if argv else "?", exc)
        return ""


VERSION_CACHE: dict[str, str] = {}


def _cached_version(name: str, argv: list[str]) -> str:
    if name not in VERSION_CACHE:
        VERSION_CACHE[name] = _fixed_cmd_output(argv)
    return VERSION_CACHE[name]


def _system_status() -> dict:
    mem = _mem_info()
    total_kb = mem.get("MemTotal", 0)
    avail_kb = mem.get("MemAvailable", 0)
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "server_uptime_seconds": int(time.time() - START_TIME),
        "time": datetime.now().isoformat(timespec="seconds"),
        "python_version": sys.version.split()[0],
        "node_version": _cached_version("node", ["node", "--version"]),
        "opencode_version": _cached_version("opencode", ["opencode", "--version"]),
        "git_version": _cached_version("git", ["git", "--version"]),
        "memory": {
            "total_mb": round(total_kb / 1024, 1) if total_kb else None,
            "available_mb": round(avail_kb / 1024, 1) if avail_kb else None,
        },
        "services": {
            "dashboard": _server_is_ours(),
            "code_server": _proc_scan(b"code-server"),
            "nginx": _proc_scan(b"nginx: master"),
            "opencode": Path("/data/data/com.termux/files/usr/bin/opencode").exists(),
            "opencode_serve": _oc_serve_info().get("running") is True,
        },
        "pid": str(Path("/proc/self").resolve().name),
    }


def _server_is_ours() -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _oc_serve_info() -> dict:
    try:
        from core.model_gateway import gateway
        try:
            gateway().ensure_available()       # status spawns serve when down
        except Exception:                     # noqa: BLE001 report as-is
            pass
        return gateway().status()
    except Exception as exc:  # noqa: BLE001
        log.error("opencode status failed: %s", exc)
        return {"running": False, "error": str(exc)}


def _factory_list() -> list[dict]:
    try:
        from core import factory
        return factory.list_projects()
    except Exception as exc:  # noqa: BLE001
        log.error("factory list failed: %s", exc)
        return []


def _read_models() -> dict:
    try:
        data = json.loads(MODELS_CONFIG.read_text(encoding="utf-8"))
        data["opencode_version"] = _cached_version("opencode", ["opencode", "--version"])
        return data
    except (OSError, ValueError) as exc:
        log.error("models config read failed: %s", exc)
        return {"active": None, "error": str(exc)}


def _write_pid() -> None:
    PID_FILE.write_text(str(Path("/proc/self").resolve().name), encoding="ascii")


def _remove_pid() -> None:
    try:
        PID_FILE.unlink()
    except FileNotFoundError:
        pass


# ------------------------------------------------------------ HTTP server ---

class Handler(BaseHTTPRequestHandler):
    server_version = "AIFactory/" + APP_VERSION
    protocol_version = "HTTP/1.1"

    # ---- plumbing ------------------------------------------------------

    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _error(self, status: int, message: str, errors: list | None = None,
               stage: str = "", tag: str = "") -> None:
        import secrets as _secrets
        from core import errreason
        trace = "af-" + _secrets.token_hex(4)
        diag = None
        if stage:          # oc/chat-path errors get a full honest diagnosis
            diag = errreason.explain(status, message, tag=tag, stage=stage)
        safe = errreason.redact(message)
        log.error("HTTP %s %s -> %s: %s [trace=%s]", self.command, self.path,
                  status, safe, trace)
        body = {"ok": False, "error": message, "trace": trace}
        if diag:
            diag["trace"] = trace
            body["diag"] = diag
        if errors:
            body["errors"] = errors
        self._json(body, status)

    # ---- P6-2 panel auth helpers ---------------------------------------

    def _cookie(self, name: str) -> str:
        for part in (self.headers.get("Cookie") or "").split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                if k.strip() == name:
                    return v.strip()
        return ""

    def _json_extra(self, payload: dict, status: int = 200,
                    set_cookies: list[str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        for c in (set_cookies or []):
            self.send_header("Set-Cookie", c)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _panel_gate(self) -> bool:
        """True when the request may proceed; on denial it has already
        answered 401 (missing session) or 403 (CSRF mismatch)."""
        from core import panelauth
        route = urlparse(self.path).path
        if not route.startswith("/api/"):
            return True
        token = self._cookie(panelauth.SESSION_COOKIE)
        if self.command == "GET":
            if route in _PANEL_PUBLIC_GET:
                return True
            if panelauth.session_token_valid(token):
                return True
            self._error(401, "ورود به پنل لازم است")
            return False
        # POST / PUT / DELETE (mutation)
        if route in _PANEL_PUBLIC_POST:
            return True
        if not panelauth.session_token_valid(token):
            self._error(401, "ورود به پنل لازم است")
            return False
        ok = panelauth.verify(
            token, self.headers.get("X-Panel-CSRF") or "",
            self._cookie(panelauth.CSRF_COOKIE))
        if not ok:
            self._error(403, "نشانه CSRF نامعتبر است")
            return False
        return True

    def _read_json(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            return None
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else None
        except (ValueError, UnicodeDecodeError):
            return None

    def _query(self) -> dict:
        raw = urlparse(self.path).query
        # P7-5 fix: http.server decodes the request line as latin-1, so raw
        # UTF-8 query values (e.g. Persian memory keys) arrive mojibaked.
        # Recover the real bytes; percent-encoded ASCII stays untouched.
        try:
            raw = raw.encode("latin-1").decode("utf-8", "strict")
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
        return {k: v[0] for k, v in parse_qs(raw).items()}

    def log_message(self, fmt, *args):  # route stdlib logging to our logger
        log.info("%s %s", self.client_address[0], fmt % args)

    # ---- static files (dashboard) --------------------------------------

    def _serve_static(self, rel: str) -> None:
        try:
            path = _resolve_safe(DASHBOARD_DIR, rel)
        except (PermissionError, OSError):
            self._error(403, "forbidden path")
            return
        if path == DASHBOARD_DIR.resolve():
            path = DASHBOARD_DIR / "index.html"
        if not path.is_file():
            self._error(404, "not found")
            return
        if path.suffix.lower() not in STATIC_EXTENSIONS:
            self._error(403, "file type not allowed")
            return
        ctype = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
                 ".png": "image/png", ".ico": "image/x-icon"}[path.suffix.lower()]
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    # ---- API handlers ---------------------------------------------------

    def do_GET(self) -> None:
        try:
            route = urlparse(self.path).path
            if not self._panel_gate():
                return
            q = self._query()

            if route in ("/", "/index.html"):
                self._serve_static("index.html")
            elif route.startswith("/static/"):
                self._serve_static(route.removeprefix("/static/"))
            elif route in ("/style.css", "/app.js", "/work.js", "/workspace.html",
                           "/workspace.css", "/factory.js", "/factory.html",
                           "/common.js", "/panel.js", "/favicon.svg",
                           "/design.css", "/design.js", "/msgtime.js",
                           "/workflow.html", "/workflow.js", "/workflow.css"):
                self._serve_static(route.removeprefix("/"))
            elif route == "/api/health":
                self._json({"ok": True, "app": APP_NAME, "version": APP_VERSION})
            elif route == "/api/status":
                self._json({"ok": True, "status": _system_status()})
            elif route == "/api/projects":
                self._api_projects_list()
            elif route == "/api/files":
                self._api_files_list(q)
            elif route == "/api/file":
                self._api_file_read(q)
            elif route == "/api/model":
                self._json({"ok": True, "model": _read_models()})
            elif route == "/api/logs":
                self._api_logs(q)
            elif route == "/api/oc/status":
                self._json({"ok": True, "opencode": _oc_serve_info()})
            elif route == "/api/oc/models":
                self._api_oc_models()
            elif route == "/api/oc/sessions":
                self._api_oc_sessions(q)
            elif route == "/api/oc/history":
                self._api_oc_history(q)
            elif route == "/api/oc/state":
                self._api_oc_state(q)
            elif route == "/api/oc/events":
                self._api_oc_events(q)
            elif route == "/api/oc/permissions":
                self._api_oc_permissions(q)
            elif route == "/api/factory/projects":
                self._json({"ok": True, "projects": _factory_list()})
            elif route == "/api/agents":
                self._api_agents_list()
            elif route == "/api/agents/view":
                self._api_agents_view(q)
            elif route == "/api/requests":
                self._api_requests_view(q)
            elif route == "/api/requests/view":
                self._api_requests_view(q)
            elif route == "/api/tasks":
                self._api_tasks_list(q)
            elif route == "/api/tasks/view":
                self._api_tasks_view(q)
            elif route == "/api/tasks/ask":
                self._api_tasks_ask(q)
            elif route == "/api/reasoning/models":
                self._api_reasoning_models(q)
            elif route == "/api/reasoning/settings":
                self._api_reasoning_settings_get()
            elif route == "/api/interactions":
                self._api_interactions_list(q)
            elif route == "/api/interactions/events":
                self._api_interactions_events(q)
            elif route == "/api/tasks/stats":
                self._api_tasks_stats()
            elif route == "/api/pipeline":
                self._api_pipeline_list(q)
            elif route == "/api/pipeline/view":
                self._api_pipeline_view(q)
            elif route == "/api/orchestrator":
                self._api_orchestrator_status()
            elif route == "/api/memory":
                self._api_memory_get(q)
            elif route == "/api/memory/namespaces":
                self._api_memory_namespaces()
            elif route == "/api/memory/agents":
                self._api_memory_agents()
            elif route == "/api/factory/plan":
                self._api_factory_plan_view(q)
            elif route == "/api/factory/generation":
                self._api_factory_generation_view(q)
            elif route == "/api/factory/check":
                self._api_factory_check(q)
            elif route == "/api/factory/quality":
                self._api_factory_quality(q)
            elif route == "/api/quality/gate":
                self._api_quality_gate_view(q)
            elif route == "/api/workflow/state":
                self._api_workflow_state(q)
            elif route == "/api/workflow/artifact":
                self._api_workflow_artifact(q)
            elif route == "/api/workflow/events":
                self._api_workflow_events(q)
            elif route == "/api/factory/types":
                self._api_factory_types(q)
            elif route == "/api/deploy/dests":
                self._api_deploy_dests_list()
            elif route == "/api/deploy/history":
                self._api_deploy_history(q)
            elif route == "/api/factory/runs":
                self._api_factory_runs()
            elif route == "/api/factory/runlog":
                self._api_factory_runlog(q)
            elif route == "/api/factory/history":
                self._api_factory_history(q)
            elif route == "/api/factory/export":
                self._api_factory_export_list_or_download(q)
            elif route == "/api/factory/version":
                self._api_factory_version_get(q)
            # AF-03: موتور اندروید + Release Center
            elif route == "/api/engines":
                self._api_engines_list()
            elif route == "/api/factory/android/inspect":
                self._api_android_inspect(q)
            elif route == "/api/factory/android/status":
                self._api_android_status(q)
            elif route == "/api/factory/android/releases":
                self._api_android_releases(q)
            elif route == "/api/factory/android/key":
                self._api_android_key_info(q)
            elif route == "/api/factory/android/store-requirements":
                from core import release_center
                self._json({"ok": True, **release_center.store_requirements()})
            elif route == "/api/factory/android/download":
                self._api_android_download(q)
            # AF-24: Smart Release — انتشار/تحویل/همگامسازی با درخواست فارسی
            elif route == "/api/smart-release/status":
                self._api_smart_release_status()
            elif route == "/api/smart-release/plan":
                self._api_smart_release_plan(q)
            elif route == "/api/smart-release/last":
                self._api_smart_release_last(q)
            elif route == "/api/smart-release/reports":
                self._api_smart_release_reports(q)
            elif route == "/api/smart-release/detect":
                self._api_smart_release_detect(q)
            elif route == "/api/smart-release/repo":
                self._api_smart_release_repo(q)
            elif route == "/api/panel/state":
                self._api_panel_state()
            else:
                self._error(404, "unknown endpoint")
        except PermissionError as exc:
            self._error(403, str(exc))
        except FileNotFoundError as exc:
            self._error(404, str(exc))
        except OcError as exc:
            self._error(exc.status, exc.message, stage="opencode")
        except Exception as exc:  # noqa: BLE001  (log full detail)
            log.exception("unhandled GET error")
            self._error(500, f"internal error: {exc}")

    def do_POST(self) -> None:
        try:
            route = urlparse(self.path).path
            if not self._panel_gate():
                return
            if route == "/api/projects":
                self._api_projects_create()
            elif route == "/api/oc/sessions":
                self._api_oc_session_create()
            elif route == "/api/oc/message":
                self._api_oc_message()
            elif route == "/api/oc/test-model":
                self._api_oc_test_model()
            elif route == "/api/oc/interrupt":
                self._api_oc_interrupt()
            elif route == "/api/oc/permission":
                self._api_oc_permission_reply()
            elif route == "/api/agents":
                self._api_agents_create()
            elif route == "/api/agents/update":
                self._api_agents_update()
            elif route == "/api/agents/delete":
                self._api_agents_delete()
            elif route == "/api/agents/validate":
                self._api_agents_validate()
            elif route == "/api/requests/intake":
                self._api_requests_intake()
            elif route == "/api/requests/answer":
                self._api_requests_answer()
            elif route == "/api/requests/plan":
                self._api_requests_plan()
            elif route == "/api/requests/approval-token":
                self._api_requests_approval_token()
            elif route == "/api/requests/approve":
                self._api_requests_approve()
            elif route == "/api/tasks":
                self._api_tasks_add()
            elif route == "/api/tasks/cancel":
                self._api_tasks_cancel()
            elif route == "/api/tasks/release":
                self._api_tasks_release()
            elif route == "/api/tasks/retry":
                self._api_tasks_retry()
            elif route == "/api/tasks/answer":
                self._api_tasks_answer()
            elif route == "/api/reasoning/settings":
                self._api_reasoning_settings_post()
            elif route == "/api/interactions/answer":
                self._api_interactions_answer()
            elif route == "/api/interactions/cancel":
                self._api_interactions_cancel()
            elif route == "/api/interactions/archive":
                self._api_interactions_archive()
            elif route == "/api/tasks/recover":
                self._api_tasks_recover()
            elif route == "/api/pipeline/create":
                self._api_pipeline_create()
            elif route == "/api/pipeline/approve":
                self._api_pipeline_approve()
            elif route == "/api/orchestrator/start":
                self._api_orchestrator_start()
            elif route == "/api/orchestrator/stop":
                self._api_orchestrator_stop()
            elif route == "/api/memory/set":
                self._api_memory_set()
            elif route == "/api/memory/delete":
                self._api_memory_delete()
            elif route == "/api/memory/agents/reset":
                self._api_memory_agents_reset()
            elif route == "/api/factory/projects":
                self._api_factory_project_create()
            elif route == "/api/factory/rename":
                self._api_factory_project_rename()
            elif route == "/api/factory/delete":
                self._api_factory_project_delete()
            elif route == "/api/factory/plan":
                self._api_factory_plan()
            elif route == "/api/factory/approve":
                self._api_factory_approve()
            elif route == "/api/factory/generate":
                self._api_factory_generate()
            elif route == "/api/factory/scaffold":
                self._api_factory_scaffold()
            elif route == "/api/factory/testrun":
                self._api_factory_testrun()
            elif route == "/api/quality/gate":
                self._api_quality_gate()
            elif route == "/api/workflow/start":
                self._api_workflow_start()
            elif route == "/api/workflow/run":
                self._api_workflow_run()
            elif route == "/api/workflow/approve":
                self._api_workflow_approve()
            elif route == "/api/workflow/reject":
                self._api_workflow_reject()
            elif route == "/api/workflow/revise":
                self._api_workflow_revise()
            elif route == "/api/workflow/retry":
                self._api_workflow_retry()
            elif route == "/api/workflow/goal":
                self._api_workflow_goal()
            elif route == "/api/workflow/ask":
                self._api_workflow_ask()
            elif route == "/api/workflow/answer":
                self._api_workflow_answer()
            elif route == "/api/factory/deps":
                self._api_factory_deps()
            elif route == "/api/factory/run":
                self._api_factory_run_start()
            elif route == "/api/factory/stop":
                self._api_factory_run_stop()
            elif route == "/api/factory/fix":
                self._api_factory_fix()
            elif route == "/api/factory/autofix":
                self._api_factory_autofix()
            elif route == "/api/factory/export":
                self._api_factory_export_create()
            elif route == "/api/factory/release":
                self._api_factory_release_create()
            elif route == "/api/factory/version":
                self._api_factory_version_set()
            elif route == "/api/deploy/dests":
                self._api_deploy_dests_save()
            elif route == "/api/deploy/preflight":
                self._api_deploy_preflight()
            elif route == "/api/deploy/start":
                self._api_deploy_start()
            elif route == "/api/deploy/rollback":
                self._api_deploy_rollback()
            elif route == "/api/deploy/stop":
                self._api_deploy_stop()
            elif route == "/api/file/rename":
                self._api_file_rename()
            # AF-03: موتور اندروید + Release Center
            elif route == "/api/factory/android/build":
                self._api_android_build()
            elif route == "/api/smart-release/plan":
                self._api_smart_release_plan_post()
            elif route == "/api/smart-release/run":
                self._api_smart_release_run()
            elif route == "/api/smart-release/chat":
                self._api_smart_release_chat()
            elif route == "/api/smart-release/confirm":
                self._api_smart_release_confirm()
            elif route == "/api/smart-release/prepare":
                self._api_smart_release_prepare()
            elif route == "/api/factory/android/key":
                self._api_android_key_create()
            elif route == "/api/factory/android/release/state":
                self._api_android_release_state()
            elif route == "/api/factory/android/store-requirements":
                self._api_android_store_req_save()
            elif route == "/api/panel/state":
                self._error(405, "method not allowed")
            elif route == "/api/panel/init":
                self._api_panel_init()
            elif route == "/api/panel/login":
                self._api_panel_login()
            elif route == "/api/panel/logout":
                self._api_panel_logout()
            else:
                self._error(404, "unknown endpoint")
        except PermissionError as exc:
            self._error(403, str(exc))
        except FileExistsError as exc:
            self._error(409, str(exc))
        except OcError as exc:
            self._error(exc.status, exc.message, stage="opencode")
        except Exception as exc:  # noqa: BLE001
            log.exception("unhandled POST error")
            self._error(500, f"internal error: {exc}")

    def do_PUT(self) -> None:
        try:
            route = urlparse(self.path).path
            # AF-26 / C1 — mutating requests must pass the same panel session
            # + CSRF gate as POST and DELETE.  Without this, any local caller
            # could overwrite project source that is later executed by run/
            # testrun/deploy, defeating the whole auth model.
            if not self._panel_gate():
                return
            if route == "/api/file":
                self._api_file_write()
            else:
                self._error(404, "unknown endpoint")
        except PermissionError as exc:
            self._error(403, str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("unhandled PUT error")
            self._error(500, f"internal error: {exc}")

    def do_DELETE(self) -> None:
        try:
            route = urlparse(self.path).path
            if not self._panel_gate():
                return
            if route == "/api/file":
                self._api_file_delete(self._query())
            else:
                self._error(404, "unknown endpoint")
        except PermissionError as exc:
            self._error(403, str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("unhandled DELETE error")
            self._error(500, f"internal error: {exc}")

    # ---- P6-2 panel auth endpoints --------------------------------------
    # Passwords/tokens never appear in any log line.

    def _panel_cookies(self, session: str, csrf: str) -> list[str]:
        from core import panelauth
        return [
            f"{panelauth.SESSION_COOKIE}={session}; Path=/; HttpOnly; "
            f"SameSite=Lax; Max-Age={panelauth.SESSION_TTL}",
            f"{panelauth.CSRF_COOKIE}={csrf}; Path=/; SameSite=Strict",
        ]

    def _api_panel_state(self) -> None:
        from core import panelauth
        self._json(panelauth.state())

    def _api_panel_init(self) -> None:
        from core import panelauth
        data = self._read_json() or {}
        password = data.get("password")
        try:
            panelauth.init(password)
        except panelauth.PanelAuthError as exc:
            self._error(exc.status, exc.message)
            return
        try:
            session, csrf = panelauth.login(password)
        except panelauth.PanelAuthError as exc:
            self._error(exc.status, exc.message)
            return
        log.info("panel init: secure admin credential created; auto-login ok")
        self._json_extra({"ok": True, "initialized": True, "login": "ok"},
                         set_cookies=self._panel_cookies(session, csrf))

    def _api_panel_login(self) -> None:
        from core import panelauth
        data = self._read_json() or {}
        password = data.get("password") or ""
        try:
            session, csrf = panelauth.login(password)
        except panelauth.PanelAuthError as exc:
            log.warning("panel login rejected (status=%s)", exc.status)
            self._error(exc.status, exc.message)
            return
        log.info("panel login: ok")
        self._json_extra({"ok": True, "login": "ok"},
                         set_cookies=self._panel_cookies(session, csrf))

    def _api_panel_logout(self) -> None:
        from core import panelauth
        token = self._cookie(panelauth.SESSION_COOKIE)
        panelauth.logout(token)
        log.info("panel logout: session cleared")
        self._json_extra({"ok": True, "logout": "ok"}, set_cookies=[
            f"{panelauth.SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; "
            "Max-Age=0",
            f"{panelauth.CSRF_COOKIE}=; Path=/; SameSite=Strict; Max-Age=0",
        ])

    # ---- OpenCode bridge (AI Workspace) ---------------------------------
    # All calls here are real HTTP calls to OpenCode v2.2.6. No command
    # execution, no shell, secrets stay backend-side (Basic auth inside).

    def _api_oc_models(self) -> None:
        from core.model_gateway import gateway
        self._json({"ok": True, "models": gateway().list_models()})

    def _api_oc_sessions(self, q: dict) -> None:
        from core.model_gateway import gateway
        project = q.get("project", "")
        from core.paths import project_name_valid
        if not project_name_valid(project):
            raise PermissionError("invalid project name")
        self._json({"ok": True, "sessions": gateway().sessions(project)})

    def _api_oc_history(self, q: dict) -> None:
        from core.model_gateway import gateway
        session = q.get("session") or ""
        if not session.startswith("ses_") or "/" in session:
            self._error(400, "bad session id")
            return
        history = gateway().history(session)
        # a session is only visible if its directory lies inside projects/
        if "/projects/" not in history.get("directory", ""):
            raise PermissionError("session outside projects/ is not accessible")
        self._json({"ok": True, **history})

    def _api_oc_state(self, q: dict) -> None:
        from core.model_gateway import gateway
        session = q.get("session") or ""
        if not session.startswith("ses_") or "/" in session:
            self._error(400, "bad session id")
            return
        try:
            st = gateway().state(session)
        except OcError as exc:
            # AF-23: exc.code didn't exist on OcError (proven real bug) — any
            # bridge error during a state poll crashed into a confusing 500.
            self._error(exc.status, exc.message, stage="state")
            return
        if "/projects/" not in st.get("directory", ""):
            raise PermissionError("session outside projects/ is not accessible")
        st.pop("directory", None)               # no internal paths to browser
        self._json({"ok": True, **st})

    def _api_oc_permissions(self, q: dict) -> None:
        from core.model_gateway import gateway
        raw = q.get("session") or ""
        session = (raw[0] if isinstance(raw, list) else str(raw)).strip()
        if not session.startswith("ses_") or "/" in session:
            self._error(400, "bad session id")
            return
        items = gateway().permissions(session)
        self._json({"ok": True, "session": session,
                    "permissions": items, "pending_count": len(items),
                    "awaiting_permission": bool(items)})

    def _api_oc_events(self, q: dict) -> None:
        """SSE proxy: stream live OpenCode events for ONE session only.

        AF-23 (hole E, proven): the old `urlopen(timeout=30)` killed the
        whole stream whenever the model stayed silent 30 s (long tool run /
        long reasoning) — the browser flipped to unknown mid-turn. Now the
        upstream read timeout is only a liveness check: on timeout or an
        upstream close we reconnect and keep the browser stream alive with
        SSE comment heartbeats. A hard per-connection cap still exists so
        no proxy thread lives forever; the browser EventSource reconnects
        transparently and the turn-end history reload (hole B fix) closes
        any text gap honestly.
        """
        import socket as _socket
        from core import opencode_bridge
        from http.client import IncompleteRead
        from urllib.error import URLError
        from urllib.request import Request, urlopen
        session = q.get("session") or ""
        if not session.startswith("ses_") or "/" in session:
            self._error(400, "bad session id")
            return
        try:
            opencode_bridge.ensure_running()
        except OcError as exc:
            self._error(exc.status, exc.message, stage="events")
            return
        # AF-29 — resolve the project ONCE so every recorded event is scoped.
        event_project = ""
        try:
            loc = opencode_bridge._session_location(session) or ""
            if "/projects/" in loc:
                event_project = loc.split("/projects/", 1)[1].split("/")[0]
        except Exception:  # noqa: BLE001 — recording still works without a label
            pass
        token = opencode_bridge.load_token()
        req = Request(OC_BASE_URL + "/api/event")
        req.add_header("Authorization", opencode_bridge._auth_header(token))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        forwarded = 0
        reconnects = 0
        upstream_deadline = time.time() + 1800.0   # hard cap: 30 min per browser connection
        try:
            self.wfile.write(b"data: {\"ok\": true, \"proxy\": \"ai-factory\"}\n\n")
            self.wfile.flush()
            while time.time() < upstream_deadline:
                try:
                    with urlopen(req, timeout=15) as upstream:
                        buffer = b""
                        while True:
                            chunk = upstream.read(4096)
                            if not chunk:
                                break      # upstream closed cleanly → reconnect below
                            buffer += chunk
                            while b"\n" in buffer:
                                line, buffer = buffer.split(b"\n", 1)
                                text = line.decode("utf-8", errors="replace").strip()
                                if not text.startswith("data:"):
                                    continue
                                try:
                                    evt = json.loads(text[5:].strip())
                                except ValueError:
                                    continue
                                etype = str(evt.get("type") or "")
                                # permission.* is NOT session.* in real OpenCode.
                                if not (etype.startswith("session.") or
                                        etype.startswith("permission.")):
                                    continue
                                data = evt.get("data") or {}
                                if data.get("sessionID") != session:
                                    continue
                                # AF-29 — record REAL tool/permission/outcome
                                # events before they reach the browser.
                                if (evt["type"] in opencode_bridge.OBSERVED_EVENTS
                                        or isinstance(data.get("part"), dict)
                                        and str((data.get("part") or {}).get("type", ""))
                                        .startswith("tool")):
                                    try:
                                        from core import agent_events
                                        agent_events.record(evt, event_project, session)
                                    except Exception:  # noqa: BLE001
                                        log.warning("tool event recording failed",
                                                    exc_info=True)
                                keep = {"type": evt["type"], "data": data}
                                perm = opencode_bridge.permission_event_data(evt)
                                if perm is not None:
                                    keep["permission"] = perm
                                body = ("data: " + json.dumps(keep, ensure_ascii=False) + "\n\n").encode("utf-8")
                                self.wfile.write(body)
                                self.wfile.flush()
                                forwarded += 1
                except IncompleteRead:
                    pass               # upstream cut mid-stream → reconnect below (seen in real logs)
                except (_socket.timeout, TimeoutError):
                    pass               # 15 s without upstream data → liveness ping below
                except (BrokenPipeError, ConnectionError):
                    raise              # browser side is gone — exit the whole proxy
                except OSError:
                    raise              # browser socket errors are not reconnectable
                except URLError as exc:
                    if isinstance(getattr(exc, "reason", None), (_socket.timeout, TimeoutError)):
                        pass           # same liveness case, wrapped by URLError
                    else:
                        raise
                if reconnects >= 120:  # never loop forever even if serve keeps dying
                    giveup = json.dumps({"type": "proxy.giveup", "data": {
                        "message": "بازپذیری مکرر upstream — جریان بسته شد"}}, ensure_ascii=False)
                    self.wfile.write(("data: " + giveup + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                    break
                reconnects += 1
                try:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionError, OSError):
                    return
        except (BrokenPipeError, ConnectionError, OSError):
            pass  # browser closed the stream — expected
        except Exception as exc:  # noqa: BLE001
            log.error("sse proxy for %s failed: %s", session, exc)
        finally:
            log.info("sse proxy closed session=%s forwarded=%s reconnects=%s",
                     session, forwarded, reconnects)
            self.close_connection = True

    def _api_oc_session_create(self) -> None:
        from core.model_gateway import gateway
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        project = str(data.get("project", ""))
        title = str(data.get("title", "")).strip() or None
        created = gateway().create_session(project, title)
        log.info("workspace session created: %s", created.get("id"))
        self._json({"ok": True, "session": created}, 201)

    def _api_oc_message(self) -> None:
        from core.model_gateway import gateway
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        project = str(data.get("project", ""))
        session = str(data.get("session_id", ""))
        text = str(data.get("text", ""))
        model = str(data.get("model", "") or "").strip()
        variant = str(data.get("variant", "") or "").strip() or None
        if not session.startswith("ses_") or "/" in session:
            self._error(400, "bad session id")
            return
        result = gateway().prompt(project, session, text, model or None,
                                        variant=variant)
        self._json({"ok": True, **result})

    def _api_oc_test_model(self) -> None:
        """AF-23: low-cost provider connection test with honest diagnosis."""
        from core.model_gateway import gateway
        data = self._read_json()
        if data is None:
            self._error(400, "بدنهٔ درخواست نامعتبر")
            return
        project = str(data.get("project", ""))
        provider_id = str(data.get("providerID", "")).strip()
        model_id = str(data.get("modelID", "")).strip()
        from core.paths import project_name_valid
        if not project_name_valid(project):
            raise PermissionError("invalid project name")
        if not provider_id or not model_id:
            self._error(400, "providerID و modelID لازم است")
            return
        try:
            res = gateway().test_model(project, provider_id, model_id,
                                             timeout_s=int(data.get("timeout_s") or 60))
        except OcError as exc:
            self._error(exc.status, exc.message, stage="test-connection",
                        tag=exc.tag)
            return
        log.info("provider test ok: %s/%s (%sms)", provider_id, model_id,
                 res.get("elapsed_ms"))
        self._json({"ok": True, **res})

    def _api_oc_interrupt(self) -> None:
        from core.model_gateway import gateway
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        session = str(data.get("session_id", ""))
        if not session.startswith("ses_") or "/" in session:
            self._error(400, "bad session id")
            return
        interrupted = gateway().interrupt(session)
        self._json({"ok": True, "interrupted": interrupted})

    def _api_oc_permission_reply(self) -> None:
        from core.model_gateway import gateway
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        session = str(data.get("session_id", ""))
        request_id = str(data.get("request_id", ""))
        decision = str(data.get("decision", ""))
        if not session.startswith("ses_") or "/" in session or not request_id:
            self._error(400, "bad session/request id")
            return
        # AF-56 — one-shot reply; a late UI click cannot contradict an
        # already-expired request. A reject carries a real, honest reason.
        message = ("کاربر مجوز را رد کرد" if decision == "reject" else None)
        replied = gateway().reply_once(session, request_id, decision,
                                             message=message)
        if not replied:
            self._json({"ok": False, "error": "مجوز قبلاً پاسخ داده شده یا منقضی است"})
            return
        # AF-29 — record the FULL decision + the original request details.
        try:
            from core import agent_events
            req = next((e for e in agent_events.events(session=session, limit=200)
                        if e.get("kind") == "permission.request" and
                        e.get("call_id") == request_id), None)
            agent_events.record_permission_decision(
                session, request_id, decision, actor="panel", request=req)
        except Exception:  # noqa: BLE001 — recording never blocks the reply
            log.warning("permission decision recording failed", exc_info=True)
        log.info("permission replied: session=%s request=%s decision=%s", session, request_id, decision)
        from core import action_gate
        gate = action_gate.gate_permissions(decision)
        self._json({"ok": decision in ("once", "always"),
                    "verification": gate["status"]})

    # ---- Factory engine (v0.3.0) -----------------------------------------

    def _api_factory_project_create(self) -> None:
        import os as _os
        from core import factory
        from core.paths import project_name_valid, resolve_safe
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        name = str(data.get("name", "")).strip()
        ptype = str(data.get("type", "")).strip() or "webpage"
        desc = str(data.get("description", "")).strip()[:500]
        if not project_name_valid(name):
            self._error(400, "نام پروژه نامعتبر است")
            return
        target = resolve_safe(PROJECTS_DIR, name)
        if target.exists():
            raise FileExistsError("پروژه‌ای با این نام از قبل وجود دارد")
        try:
            created = factory.create_project(name, ptype, desc)
        except ValueError as exc:
            self._error(400, str(exc))
            return
        log.info("factory project create: %s type=%s (uid=%s)",
                 name, ptype, _os.getpid())
        self._json({"ok": True, "project": created}, 201)

    def _api_factory_version_get(self, q: dict) -> None:
        """AF-12 — نسخهٔ مستقل پروژه (خواندن). GET /api/factory/version?project=…"""
        from core import release
        project = str(q.get("project", "")).strip()
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        try:
            version = release.project_version(_paths.project_dir(project))
        except FileNotFoundError:
            self._error(404, "پروژه یافت نشد: " + project)
            return
        except ValueError as exc:
            self._error(409, str(exc))
            return
        self._json({"ok": True, "project": project, "version": version})

    def _api_factory_version_set(self) -> None:
        """AF-12 — تنظیم صریح نسخهٔ پروژه (تنها راه تغییر؛ بدون افزایش خودکار).
        POST /api/factory/version {"project": …, "version": "1.2.0"}"""
        from core import release
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        project = str(data.get("project", "")).strip()
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        version = str(data.get("version", "")).strip()
        try:
            res = release.set_version(project, version)
        except FileNotFoundError:
            self._error(404, "پروژه یافت نشد: " + project)
            return
        except ValueError as exc:
            self._error(400, str(exc))
            return
        self._json({"ok": True, **res})

    def _api_factory_project_rename(self) -> None:
        from core import factory
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        old = str(data.get("old", "")).strip()
        new = str(data.get("new", "")).strip()
        if not _Name.match(old) or not _Name.match(new):
            self._error(400, "نام‌ها نامعتبرند")
            return
        if old == new:
            self._error(400, "نام جدید با قبلی یکسان است")
            return
        from core import runner
        if runner.status(old).get("running"):
            self._error(409, "ابتدا پروژه در حال اجرا را متوقف کنید")
            return
        res = factory.rename_project(old, new)
        self._json({"ok": True, **res})

    def _api_factory_project_delete(self) -> None:
        from core import factory
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        name = str(data.get("project", "")).strip()
        confirm = data.get("confirm")
        if not _Name.match(name):
            self._error(400, "نام پروژه نامعتبر است")
            return
        if not _resolve_safe(PROJECTS_DIR, name).is_dir():
            self._json({"ok": True, "deleted": False,
                        "note": "پروژه وجود ندارد (قبلاً حذف شده)"})
            return
        from core import runner
        if runner.status(name).get("running"):
            self._error(409, "ابتدا پروژه در حال اجرا را متوقف کنید")
            return
        # last real backup before destruction (outside projects/, in backups/)
        from core import exporter
        try:
            bkp = exporter.backup(name)
        except Exception as exc:  # noqa: BLE001
            log.error("pre-delete backup failed for %s: %s", name, exc)
            self._error(500, "تهیه نسخه پیش از حذف ناموفق بود؛ حذف انجام نشد")
            return
        log.info("pre-delete backup of %s -> %s", name, bkp.get("path"))
        factory.delete_project(name, confirm is True)
        self._json({"ok": True, "deleted": name, "backup": bkp.get("path")})

    def _valid_agent_or_none(self, raw) -> tuple[str | None, tuple[int, str] | None]:
        """AF-06.2 — validate an optional agent name sent from the panel UI.
        Only agents that really exist in the registry AND are enabled are
        accepted; an empty/absent value returns (None, None) = old behavior."""
        agent = str(raw or "").strip() or None
        if not agent:
            return None, None
        from core import agents as agentsmod
        try:
            a = agentsmod.get_agent(agent)
        except agentsmod.AgentError as exc:
            return None, (exc.status, exc.message)
        if not a.get("enabled"):
            return None, (400, f"عامل «{agent}» غیرفعال است")
        return agent, None

    def _api_factory_plan(self) -> None:
        from core import factory, requestcheck
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        project = str(data.get("project", "")).strip()
        goal = str(data.get("goal", "")).strip()
        # AF-06.2: optional agent selection — validated against the real
        # registry (exists + enabled); absent = previous behavior
        agent, aerr = self._valid_agent_or_none(data.get("agent"))
        if aerr:
            self._error(aerr[0], aerr[1])
            return
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        _paths.project_dir(project)  # raises FileNotFoundError if missing
        # P6-3 — validate the Persian request BEFORE the model sees it.
        rc = requestcheck.analyze(goal)
        if not rc["allowed"]:
            factory.append_history(project, "request-check",
                                   "REJECTED — " + rc["note"])
            self._error(400, rc["note"])
            return
        try:
            res = factory.plan(project, goal, agent=agent)
        except OcError as exc:
            self._error(exc.status, exc.message)
            return
        factory.append_history(project, "request-check", rc["note"])
        factory.append_history(project, "plan-requested", goal[:200])
        self._json({"ok": True, "request_check": rc, **res})

    # ---- Phase 02 — canonical Request Intelligence --------------------

    def _ri_error(self, exc: Exception) -> None:
        name = type(exc).__name__
        status = 409 if isinstance(exc, ValueError) else 400
        self._error(status, "%s: %s" % (name, str(exc)))

    def _api_requests_view(self, q: dict) -> None:
        from core import request_intelligence as ri
        rid = str(q.get("request_id") or "")
        if rid:
            rec = ri.get(rid)
            if rec is None:
                self._error(404, "request-not-found")
                return
            self._json({"ok": True, "request": ri.save_view(rec)})
            return
        self._json({"ok": True, "requests": [ri.save_view(r)
                                             for r in ri.list_requests(
                                                 str(q.get("project") or ""))]})

    def _api_requests_intake(self) -> None:
        from core import request_intelligence as ri
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            rec = ri.intake(str(data.get("text") or data.get("goal") or ""),
                            project=str(data.get("project") or ""),
                            source_channel=str(data.get("source") or "api"),
                            request_id=str(data.get("request_id") or ""))
        except (ri.RequestError, ri.RequestStoreError, ValueError) as exc:
            self._ri_error(exc)
            return
        self._json({"ok": True, "request": ri.save_view(rec)})

    def _api_requests_answer(self) -> None:
        from core import request_intelligence as ri
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            rec = ri.answer(str(data.get("request_id") or ""),
                            str(data.get("answer") or ""),
                            str(data.get("question_id") or ""))
        except (ri.RequestError, ri.RequestStoreError, ValueError) as exc:
            self._ri_error(exc)
            return
        self._json({"ok": True, "request": ri.save_view(rec)})

    def _api_requests_plan(self) -> None:
        from core import request_intelligence as ri
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            rec = ri.build_plan(str(data.get("request_id") or ""))
        except (ri.RequestError, ri.RequestStoreError, ValueError) as exc:
            self._ri_error(exc)
            return
        self._json({"ok": True, "request": ri.save_view(rec)})

    def _api_requests_approval_token(self) -> None:
        from core import request_intelligence as ri
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            proposal = ri.approval_proposal(str(data.get("request_id") or ""),
                                            str(data.get("session_id") or ""))
        except (ri.RequestError, ri.RequestStoreError, ValueError) as exc:
            self._ri_error(exc)
            return
        self._json({"ok": True, "approval": proposal})

    def _api_requests_approve(self) -> None:
        from core import request_intelligence as ri
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            rec = ri.approve(str(data.get("request_id") or ""),
                             str(data.get("token") or ""),
                             str(data.get("actor") or ""),
                             project=str(data.get("project") or ""),
                             session_id=str(data.get("session_id") or ""))
        except (ri.RequestError, ri.RequestStoreError, ValueError) as exc:
            self._ri_error(exc)
            return
        self._json({"ok": True, "request": ri.save_view(rec)})

    def _api_factory_plan_view(self, q: dict) -> None:
        from core import factory
        project = q.get("project", "")
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        text = factory.plan_text(project)
        self._json({"ok": True, "project": project, "has_plan": bool(text),
                    "plan": text})

    def _api_factory_approve(self) -> None:
        from core import factory
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        project = str(data.get("project", "")).strip()
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        text = factory.plan_text(project)
        if not text:
            self._error(409, "هنوز برنامه‌ای برای تأیید وجود ندارد")
            return
        factory.append_history(project, "plan-approved", text[:400])
        factory.touch(project, "plan-approved")
        self._json({"ok": True, "plan": text})

    # ---- v0.4.0: fullstack scaffold / test run / deps ---------------------

    def _api_factory_scaffold(self) -> None:
        from core import factory, scaffold
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        project = str(data.get("project", "")).strip()
        title = str(data.get("app_title", "")).strip()[:60] or None
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        try:
            d = _paths.project_dir(project)
        except FileNotFoundError:
            self._error(404, "پروژه وجود ندارد")
            return
        meta = factory.read_meta(d)
        if meta.get("type") != "fullstack-python":
            self._error(400, "اسکفولد فقط برای نوع fullstack-python")
            return
        res = scaffold.scaffold(project, app_title=title)
        factory.append_history(project, "scaffold", "%d files" % len(res["files"]))
        self._json({"ok": True, **res})

    def _api_factory_testrun(self) -> None:
        # P6-1 (P7-3 refactor): the combined semantics live in
        # factory.testrun_combined so the panel API and the P7 orchestrator
        # run the EXACT same acceptance gate. Response shape unchanged.
        from core import factory
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        project = str(data.get("project", "")).strip()
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        try:
            res = factory.testrun_combined(project)
        except (RuntimeError, OcError) as exc:
            self._error(400, str(exc))
            return
        log.info("factory testrun: %s own_ran=%s contract_ran=%s",
                 project, (res.get("own_tests") or {}).get("ran"),
                 (res.get("contract") or {}).get("ran"))
        self._json(res)

    def _api_factory_deps(self) -> None:
        from core import runner, deps as depsmod
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        project = str(data.get("project", "")).strip()
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        try:
            res = runner.install_deps(project)
        except (RuntimeError, OcError) as exc:
            self._error(400, str(exc))
            return
        # P6-4: read-only dependency survey (never installs, offline-safe)
        try:
            res["survey"] = depsmod.survey(project)
        except (RuntimeError, OcError) as exc:
            res["survey"] = {"ok": False, "error": str(exc)}
        self._json({"ok": True, **res})

    # ---- P7-1 agent registry --------------------------------------------

    def _api_agents_list(self) -> None:
        from core import agents as agentsmod
        try:
            self._json({"ok": True, "agents": agentsmod.list_agents()})
        except agentsmod.AgentError as exc:
            self._error(exc.status, exc.message, exc.errors)
        except Exception as exc:  # noqa: BLE001
            log.exception("agents list failed")
            self._error(500, str(exc))

    def _api_agents_view(self, q: dict) -> None:
        from core import agents as agentsmod
        try:
            self._json({"ok": True,
                        "agent": agentsmod.get_agent(q.get("name", ""))})
        except agentsmod.AgentError as exc:
            self._error(exc.status, exc.message, exc.errors)

    def _api_agents_create(self) -> None:
        from core import agents as agentsmod
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            self._json({"ok": True, "agent": agentsmod.create_agent(data)}, 201)
        except agentsmod.AgentError as exc:
            self._error(exc.status, exc.message, exc.errors)

    def _api_agents_update(self) -> None:
        from core import agents as agentsmod
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        name = str(data.get("name", "")).strip()
        try:
            self._json({"ok": True, "agent": agentsmod.update_agent(name, data)})
        except agentsmod.AgentError as exc:
            self._error(exc.status, exc.message, exc.errors)

    def _api_agents_delete(self) -> None:
        from core import agents as agentsmod
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            self._json({"ok": True, **agentsmod.delete_agent(
                str(data.get("name", "")).strip())})
        except agentsmod.AgentError as exc:
            self._error(exc.status, exc.message, exc.errors)

    def _api_agents_validate(self) -> None:
        from core import agents as agentsmod
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        errors = agentsmod.validate(data, for_create=True)
        self._json({"ok": not errors, "valid": not errors, "errors": errors})

    # ---- P7-2 persistent task queue -------------------------------------

    def _api_tasks_list(self, q: dict) -> None:
        from core import tasks as tasksmod
        state = (q.get("state") or "").strip() or None
        self._json({"ok": True, "tasks": tasksmod.view(
            tasksmod.list_tasks(state))})

    def _api_tasks_view(self, q: dict) -> None:
        from core import tasks as tasksmod
        try:
            self._json({"ok": True, "task": tasksmod._iso_view(
                tasksmod.get_task(q.get("id", "")))})
        except tasksmod.TaskError as exc:
            self._error(exc.status, exc.message)

    def _api_tasks_stats(self) -> None:
        from core import tasks as tasksmod
        self._json({"ok": True, "stats": tasksmod.stats()})

    def _api_tasks_add(self) -> None:
        from core import tasks as tasksmod
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            meta = {}
            if data.get("reasoning"):
                from core import reasoning as _reasoning
                lvl = str(data["reasoning"]).lower()
                if lvl not in _reasoning.LEVELS:
                    self._error(400, "سطح تفکر نامعتبر است")
                    return
                meta["reasoning"] = lvl
            t = tasksmod.add_task(str(data.get("project", "")).strip(),
                                  str(data.get("goal", "")),
                                  str(data.get("agent", "builder")).strip()
                                  or "builder",
                                  data.get("max_attempts", 2),
                                  meta=meta)
            self._json({"ok": True, "task": tasksmod._iso_view(t)}, 201)
        except tasksmod.TaskError as exc:
            self._error(exc.status, exc.message)

    def _api_tasks_cancel(self) -> None:
        self._tasks_transition("cancel_task")

    def _api_tasks_release(self) -> None:
        self._tasks_transition("release_task", note=True)

    def _api_tasks_retry(self) -> None:
        self._tasks_transition("retry_task")

    def _api_tasks_recover(self) -> None:
        from core import orchestrator, tasks as tasksmod
        # AF-35 — recovering while workers are alive races a live claim and
        # can execute the same task twice.
        if orchestrator.running():
            self._error(409, "بازیابی تنها در زمانی مجاز است که workerها متوقفاند")
            return
        recovered = tasksmod.recover_orphans()
        self._json({"ok": True, "recovered": tasksmod.view(recovered),
                    "count": len(recovered)})

    def _api_tasks_ask(self, q: dict) -> None:
        from core import tasks as tasksmod
        try:
            t = tasksmod.get_task(q.get("id", ""))
        except tasksmod.TaskError as exc:
            self._error(exc.status, exc.message)
            return
        if t.get("state") != "awaiting_user":
            self._json({"ok": True, "awaiting": False, "state": t["state"]})
            return
        # The one-shot token IS the answer handle the caller needs; it is not
        # a credential. Generic task lists still redact it via _iso_view.
        ask = dict(t.get("ask") or {})
        ask.pop("decision", None)
        self._json({"ok": True, "awaiting": True, "task": t["id"],
                    "project": t["project"], "ask": ask})

    def _api_tasks_answer(self) -> None:
        from core import tasks as tasksmod
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            t = tasksmod.answer_task(
                str(data.get("id", "")).strip(),
                str(data.get("token", "")).strip(),
                str(data.get("answer", "")).strip())
        except tasksmod.TaskError as exc:
            self._error(exc.status, exc.message)
            return
        self._json({"ok": True, "resumed": t["id"], "state": t["state"]})

    # ---- AF-51: reasoning control + live interaction center -------------
    def _api_reasoning_models(self, q) -> None:
        from core.model_gateway import gateway
        from core import reasoning
        goal = str((q.get("goal") or [""])[0] if isinstance(q.get("goal"), list)
                   else (q.get("goal") or ""))
        kind = str((q.get("kind") or [""])[0] if isinstance(q.get("kind"), list)
                   else (q.get("kind") or ""))
        self._json({"ok": True, "levels": list(reasoning.LEVELS),
                    "default": reasoning.default_level(),
                    "models": reasoning.catalog(gateway().list_models(),
                                                goal, kind)})

    def _api_reasoning_settings_get(self) -> None:
        from core import reasoning
        self._json({"ok": True, "reasoning_level": reasoning.default_level(),
                    "levels": list(reasoning.LEVELS)})

    def _api_reasoning_settings_post(self) -> None:
        from core import reasoning
        data = self._read_json() or {}
        try:
            rec = reasoning.set_default_level(data.get("reasoning_level"))
        except ValueError as exc:
            self._error(400, str(exc))
            return
        self._json({"ok": True, **rec})

    @staticmethod
    def _humanize_ask(question: str, reason: str) -> tuple[str, str]:
        """AF-54 — replace internal policy names with plain human language."""
        q = str(question or "")
        r = str(reason or "")
        if any(k in q for k in ("Policy", "policy-denied", "destructive-command",
                                "outside-project-path", "server-start-forbidden",
                                "network-forbidden", "Step interrupted")):
            return ("عامل تلاش کرد کاری خارج از محدودهٔ ایمن پروژه انجام دهد. "
                    "ادامهٔ ایمن بیخطر است یا بهتر است متوقف شود؟",
                    "محدودیت امنیتی پروژه مانع ادامهٔ خودکار شد.")
        if r.startswith("عملیات پرخطر"):
            return q, r
        if r:
            return q, r
        return q, "برای ادامهٔ درست، نظر شما لازم است."

    def _interactions(self, project: str = "") -> dict:
        from core import paths, tasks as tasksmod
        out = []
        for t in tasksmod.list_tasks():
            ask = t.get("ask") or {}
            if not ask:
                continue
            tid = t.get("id")
            pname = t.get("project") or ""
            if project and pname != project:
                continue
            st = t.get("state")
            created = float(ask.get("created") or 0)
            age = time.time() - created if created else 0
            try:
                paths.project_dir(pname)       # deleted project => not active
                project_ok = True
            except (PermissionError, FileNotFoundError):
                project_ok = False
            ctx = ask.get("context") or {}
            decision = ctx.get("decision") if isinstance(ctx.get("decision"), dict) else {}
            if ask.get("archived"):
                status = "archived"
            elif ask.get("answered") or t.get("ask_answer"):
                status = "answered"
            elif st == "awaiting_user":
                if not project_ok:
                    status = "orphaned"
                elif age > 24 * 3600:
                    status = "expired"
                else:
                    status = "open"
            elif st in ("cancelled", "failed", "done"):
                status = "task_stopped"
            else:
                status = "task_stopped"
            raw_reason = str(ctx.get("reason") or decision.get("reason") or
                             (ctx.get("ask") or {}).get("reason") or "")
            question, reason = self._humanize_ask(ask.get("question"), raw_reason)
            out.append({
                "id": tid, "task": tid, "project": pname,
                "agent": t.get("agent"), "kind": t.get("kind"),
                "stage": ctx.get("stage") or st,
                "question": question, "reason": reason[:300],
                "options": ask.get("options") or [],
                "recommended": str(decision.get("recommended") or
                                   ctx.get("recommended") or "")[:120],
                "action": ("confirm" if decision.get("decision") == "CONFIRM"
                           else "resume" if ctx.get("stage") == "loop" else "question"),
                "token": ask.get("token") if status == "open" else "",
                "status": status, "active": status == "open",
                "created": ask.get("created") or t.get("updated"),
                "answered": ask.get("answered"),
                "session": ctx.get("session_id") or "",
            })
        out.sort(key=lambda x: (x["active"], float(x.get("created") or 0)), reverse=True)
        return {"items": out[:40],
                "active_count": sum(1 for x in out if x["active"])}

    def _api_interactions_list(self, q) -> None:
        raw = q.get("project") or ""
        project = (raw[0] if isinstance(raw, list) else str(raw)).strip()
        self._json({"ok": True, **self._interactions(project)})

    def _api_interactions_archive(self) -> None:
        from core import tasks as tasksmod
        data = self._read_json() or {}
        ids = data.get("ids") or []
        if not isinstance(ids, list):
            self._error(400, "ids must be a list")
            return
        if not ids and data.get("all_old"):
            ids = [x["task"] for x in self._interactions()["items"]
                   if x["status"] in ("orphaned", "expired", "task_stopped")]
        res = tasksmod.archive_asks([str(x) for x in ids],
                                    note="AF-54 batch archive (no data deleted)")
        self._json({"ok": True, **res})

    def _api_interactions_answer(self) -> None:
        from core import tasks as tasksmod
        data = self._read_json() or {}
        try:
            t = tasksmod.answer_task(
                str(data.get("id") or data.get("task") or "").strip(),
                str(data.get("token") or "").strip(),
                str(data.get("answer") or "").strip())
        except tasksmod.TaskError as exc:
            self._error(exc.status, exc.message)
            return
        self._json({"ok": True, "resumed": t["id"], "state": t["state"]})

    def _api_interactions_cancel(self) -> None:
        from core import tasks as tasksmod
        data = self._read_json() or {}
        tid = str(data.get("id") or data.get("task") or "").strip()
        token = str(data.get("token") or "").strip()
        try:
            t = tasksmod.get_task(tid)
        except tasksmod.TaskError as exc:
            self._error(exc.status, exc.message)
            return
        ask = t.get("ask") or {}
        if t.get("state") != "awaiting_user" or token != ask.get("token"):
            self._error(403, "درخواست نامعتبر است یا قبلاً پاسخ داده شده است")
            return
        out = tasksmod.cancel_task(tid)
        self._json({"ok": True, "cancelled": out["id"], "state": out["state"]})

    def _api_interactions_events(self, q) -> None:
        """SSE driven by the REAL task store (not a decorative widget)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        last = None
        for _ in range(150):                 # bounded; browser reconnects
            if self._stop_event.is_set() if hasattr(self, "_stop_event") else False:
                break
            payload = self._interactions()
            items = payload["items"]
            sig = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if sig != last:
                try:
                    self.wfile.write(("data: " + json.dumps(
                        payload, ensure_ascii=False) + "\n\n").encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break          # browser disconnected — normal SSE close
                last = sig
            time.sleep(2.0)

    # ---- AF-18: integrated request→delivery pipeline ---------------------

    def _api_pipeline_list(self, q) -> None:
        from core import pipeline
        raw = q.get("state") or ""
        state = (raw[0] if isinstance(raw, list) else str(raw)) or None
        recs = pipeline.list_pipelines(state)
        self._json({"ok": True,
                    "pipelines": [_pview(r) for r in recs[:50]]})

    def _api_pipeline_view(self, q) -> None:
        from core import pipeline
        raw = q.get("id") or ""
        pid = raw[0] if isinstance(raw, list) else str(raw)
        try:
            rec = pipeline.get(pid)
        except pipeline.PipelineError as exc:
            self._error(exc.status, exc.message)
            return
        self._json({"ok": True, "pipeline": _pview(rec, detail=True)})

    def _api_pipeline_create(self) -> None:
        from core import pipeline
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            rec = pipeline.create(str(data.get("project", "")).strip(),
                                  str(data.get("goal", "")),
                                  str(data.get("agent", "builder")).strip()
                                  or "builder",
                                  reasoning=(str(data.get("reasoning") or "").lower()
                                             or None))
        except pipeline.PipelineError as exc:
            self._error(exc.status, exc.message)
            return
        self._json({"ok": True, "pipeline": rec}, 201)

    def _api_pipeline_approve(self) -> None:
        from core import pipeline
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            rec = pipeline.approve(str(data.get("id", "")).strip(),
                                   data.get("ok") is True)
        except pipeline.PipelineError as exc:
            self._error(exc.status, exc.message)
            return
        self._json({"ok": True, "pipeline": rec})

    def _tasks_transition(self, fn: str, note: bool = False) -> None:
        from core import tasks as tasksmod
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            if note:
                t = getattr(tasksmod, fn)(str(data.get("id", "")).strip(),
                                          str(data.get("note", "")))
            else:
                t = getattr(tasksmod, fn)(str(data.get("id", "")).strip())
            self._json({"ok": True, "task": tasksmod._iso_view(t)})
        except tasksmod.TaskError as exc:
            self._error(exc.status, exc.message)

    # ---- P7-3 orchestrator ----------------------------------------------

    def _api_orchestrator_status(self) -> None:
        from core import orchestrator
        self._json({"ok": True, "orchestrator": orchestrator.status()})

    def _api_orchestrator_start(self) -> None:
        from core import orchestrator
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            workers = int(data.get("workers", 1))
        except (TypeError, ValueError):
            self._error(400, "workers باید عدد باشد (۱ یا ۲)")
            return
        self._json(orchestrator.start(workers))

    def _api_orchestrator_stop(self) -> None:
        from core import orchestrator
        self._json(orchestrator.stop())

    # ---- P7-5 shared memory ----------------------------------------------

    def _api_memory_get(self, q: dict) -> None:
        from core import memory as memmod
        ns = (q.get("ns") or "").strip()
        try:
            if q.get("key"):
                e = memmod.get_value(ns, q["key"])
                if e is None:
                    self._error(404, "کلید یافت نشد")
                    return
                self._json({"ok": True, "ns": ns,
                            "key": q["key"].strip(), **e})
            else:
                self._json({"ok": True, "ns": ns,
                            "entries": memmod.namespace_dump(ns)})
        except memmod.MemoryError as exc:
            self._error(exc.status, exc.message)

    def _api_memory_namespaces(self) -> None:
        from core import memory as memmod
        self._json({"ok": True, "namespaces": memmod.list_namespaces()})

    def _api_memory_agents(self) -> None:
        from core import memory as memmod
        self._json({"ok": True, "perf": memmod.agent_report()})

    def _api_memory_set(self) -> None:
        from core import memory as memmod
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            self._json({"ok": True, "entry": memmod.set_value(
                str(data.get("ns", "")).strip(),
                str(data.get("key", "")),
                str(data.get("value", "")),
                str(data.get("by", "panel")))})
        except memmod.MemoryError as exc:
            self._error(exc.status, exc.message)

    def _api_memory_delete(self) -> None:
        from core import memory as memmod
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            self._json({"ok": True, **memmod.delete_value(
                str(data.get("ns", "")).strip(),
                str(data.get("key", "")))})
        except memmod.MemoryError as exc:
            self._error(exc.status, exc.message)

    def _api_memory_agents_reset(self) -> None:
        from core import memory as memmod
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        self._json({"ok": True, **memmod.reset_agent(
            str(data.get("name", "")).strip())})

    def _api_factory_generate(self) -> None:
        from core import factory, requestcheck
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        project = str(data.get("project", "")).strip()
        goal = str(data.get("goal", "")).strip()
        use_plan = bool(data.get("use_plan", False))
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        if use_plan and not goal:
            plan_txt = factory.plan_text(project)
            if not plan_txt:
                self._error(409, "برنامه‌ای موجود نیست؛ ابتدا برنامه بسازید")
                return
            res = factory.generate(project, "اجرای برنامه تأییدشده", include_plan=plan_txt)
        elif goal:
            # P6-3 — dangerous/malicious requests never reach the model.
            rc = requestcheck.analyze(goal)
            if not rc["allowed"]:
                factory.append_history(project, "request-check",
                                       "REJECTED — " + rc["note"])
                self._error(400, rc["note"])
                return
            factory.append_history(project, "request-check", rc["note"])
            res = factory.generate(project, goal, include_plan=None)
            return self._json({"ok": True, "request_check": rc, **res})
        else:
            self._error(400, "هدف یا برنامه لازم است")
            return
        self._json({"ok": True, **res})

    def _api_factory_generation_view(self, q: dict) -> None:
        from core import factory
        project = q.get("project", "")
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        sid = factory.generation_session(project)
        if not sid:
            self._json({"ok": True, "started": False, "done": False, "text": None})
            return
        done = factory._done(sid)
        self._json({"ok": True, "started": True, "done": done,
                    "session_id": sid, "text": factory.generation_text(project)})

    # ------------------------------------------------------------------ #
    # AF-03 — موتور اندروید + Release Center                              #
    # ------------------------------------------------------------------ #
    def _api_engines_list(self) -> None:
        from core.engines import list_engines
        self._json({"ok": True, "engines": list_engines()})

    def _android_engine(self):
        from core.engines import get_engine
        try:
            return get_engine("android")
        except Exception as exc:  # EngineError / ImportError
            self._error(404, str(exc))
            return None

    def _api_android_inspect(self, q: dict) -> None:
        eng = self._android_engine()
        if eng is None:
            return
        project = q.get("project", "")
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        d = _paths.project_dir(project)
        info = eng.inspect(d)
        info["validate"] = eng.validate(d)
        self._json({"ok": True, "project": project, "info": info})

    def _api_android_status(self, q: dict) -> None:
        eng = self._android_engine()
        if eng is None:
            return
        project = q.get("project", "")
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        from core.engines import android as androidmod
        self._json({"ok": True, "project": project,
                    **androidmod.status(project)})

    def _api_android_build(self) -> None:
        eng = self._android_engine()
        if eng is None:
            return
        data = self._read_json()
        if data is None:
            self._error(400, "بدنهٔ درخواست نامعتبر")
            return
        project = str(data.get("project") or "")
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        opts = {"kind": str(data.get("kind") or "apk"),
                "sign": bool(data.get("sign")),
                "version_code": data.get("version_code") or None,
                "version_name": data.get("version_name") or None,
                "agent": str(data.get("agent") or "") or None}
        try:
            from core.engines import android as androidmod
            res = androidmod.start_build(project, opts)
            self._json({"ok": True, **res})
        except Exception as exc:
            status = getattr(exc, "status", 500)
            self._error(status, str(exc))

    def _api_android_key_info(self, q: dict) -> None:
        eng = self._android_engine()
        if eng is None:
            return
        project = q.get("project", "")
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        d = _paths.project_dir(project)
        info = eng.key_info(d)
        self._json({"ok": True, "project": project, **info})

    def _api_android_key_create(self) -> None:
        """ساخت کلید اختصاصی محصول — فقط با confirm صریح (قانون تأیید کاربر)."""
        eng = self._android_engine()
        if eng is None:
            return
        data = self._read_json()
        if data is None or not data.get("confirm"):
            self._error(400, "ساخت کلید نیازمند تأیید صریح است (confirm=true)")
            return
        project = str(data.get("project") or "")
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        d = _paths.project_dir(project)
        res = eng.create_product_key(d)
        self._json({"ok": True, "project": project, **res})

    def _api_android_releases(self, q: dict) -> None:
        from core import release_center
        project = q.get("project", "")
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        self._json({"ok": True, "project": project,
                    "releases": release_center.list_releases(project)})

    def _api_android_release_state(self) -> None:
        from core import release_center
        data = self._read_json()
        if data is None:
            self._error(400, "بدنهٔ درخواست نامعتبر")
            return
        project = str(data.get("project") or "")
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        try:
            rel = release_center.set_state(
                project, str(data.get("release_id") or ""),
                str(data.get("state") or ""),
                note=str(data.get("note") or ""),
                confirm=bool(data.get("confirm")))
            self._json({"ok": True, "release": rel})
        except (KeyError, ValueError) as exc:
            self._error(409, str(exc))

    def _api_android_store_req_save(self) -> None:
        from core import release_center
        data = self._read_json()
        if data is None:
            self._error(400, "بدنهٔ درخواست نامعتبر")
            return
        try:
            release_center.save_store_requirements(data)
            self._json({"ok": True, "saved": True})
        except ValueError as exc:
            self._error(400, str(exc))

    # ---- AF-24: Smart Release -------------------------------------------
    # ONE real operation behind one Persian request. Reuses the release R3
    # pipeline, the Android engine, the security gate and core.git_sync.

    def _api_smart_release_status(self) -> None:
        from core import smart_release as sr
        from core import git_sync
        tools = {"git": bool(__import__("shutil").which("git")),
                 "gh": bool(__import__("shutil").which("gh")),
                 "downloads": str(sr._downloads_dir() or "در دسترس نیست"),
                 "projects": len(sr.project_names())}
        self._json({"ok": True, "available": True, "tools": tools,
                    "labels": sr.LABELS, "last": sr.last()})

    def _api_smart_release_detect(self, q: dict) -> None:
        from core import smart_release as sr
        text = str(q.get("text") or "")
        self._json({"ok": True, **sr.detect_intent(text)})

    def _api_smart_release_plan(self, q: dict) -> None:
        from core import smart_release as sr
        text = str(q.get("text") or "")
        try:
            project = sr.resolve_project(q.get("project"), text) \
                if q.get("project") or text else ""
            if not project:
                self._error(400, "پروژه مشخص نشد (project یا text لازم است)")
                return
            self._json({"ok": True, **sr.plan(project, text)})
        except (PermissionError, FileNotFoundError) as exc:
            self._error(404, str(exc))
        except ValueError as exc:
            self._error(400, str(exc))

    def _api_smart_release_plan_post(self) -> None:
        data = self._read_json()
        self._api_smart_release_plan(data if data is not None else {})

    def _api_smart_release_run(self) -> None:
        from core import smart_release as sr, project_git
        data = self._read_json()
        if data is None:
            self._error(400, "بدنهٔ درخواست نامعتبر")
            return
        session = str(data.get("session_id") or "")
        project = str(data.get("project") or "") or None
        if not project and session:
            project, _ = project_git.active_project(session)
        report = sr.run(project=project, text=str(data.get("text") or ""),
                        confirm=bool(data.get("confirm")),
                        version=str(data.get("version") or "") or None,
                        push=bool(data.get("push", True)),
                        auto_git=bool(data.get("auto_git", True)),
                        git_decision=data.get("git_decision"),
                        decline_git=bool(data.get("decline_git")),
                        session_id=session)
        self._json({"ok": report.get("status") in (sr.OK, sr.PARTIAL), **report})

    def _chat_project(self, data: dict, text: str) -> tuple[str | None, str]:
        """AF-25: active project from session → explicit name → request text."""
        from core import smart_release as sr, project_git
        session = str(data.get("session_id") or "")
        project = str(data.get("project") or "") or None
        note = ""
        if not project and session:
            project, note = project_git.active_project(session)
        if not project:
            try:
                project = sr.resolve_project(text, None)
            except (ValueError, PermissionError, FileNotFoundError):
                return None, ("لطفاً نام پروژهٔ فعال را بدهید (مثلاً «معلم من» یا "
                              "teacher-android) تا عملیات دقیقاً روی همان پروژه انجام شود.")
        return project, note

    def _api_smart_release_chat(self) -> None:
        """Chat entry: a PERSIAN sentence is detected and EXECUTED, with
        in-conversation one-shot confirmation for sensitive repo actions."""
        from core import smart_release as sr, confirmations
        from core.confirmations import ConfirmError
        data = self._read_json()
        if data is None:
            self._error(400, "بدنهٔ درخواست نامعتبر")
            return
        text = str(data.get("text") or "")
        session = str(data.get("session_id") or "")
        token = str(data.get("confirm_token") or "")
        if token:
            confirmed = bool(data.get("confirm", True))
            try:
                v = confirmations.consume(token, session_id=session)
            except ConfirmError as exc:
                self._error(exc.status, exc.message)
                return
            report = sr.run(project=v["project"], text=text,
                            push=bool(data.get("push", True)),
                            git_decision=v["payload"] if confirmed else None,
                            decline_git=not confirmed, session_id=session)
            self._json({"ok": report.get("status") in (sr.OK, sr.PARTIAL),
                        "executed": confirmed, "is_release": True,
                        "confirmation_used": True, **report})
            return
        intent = sr.detect_intent(text)
        if not intent["is_release"]:
            self._json({"ok": True, "is_release": False,
                        "note": intent["note"], "executed": False,
                        "hint": "برای انتشار بگویید: «نسخه نهایی <پروژه> را بساز، "
                                "در دانلودها ذخیره کن و Git/GitHub را بهروز کن»"})
            return
        project, note = self._chat_project(data, text)
        if not project:
            self._json({"ok": True, "is_release": True, "executed": False,
                        "needs_project": True, "question_fa": note})
            return
        report = sr.run(project=project, text=text,
                        confirm=bool(data.get("confirm")),
                        push=bool(data.get("push", True)), session_id=session,
                        auto_git=bool(data.get("auto_git", True)))
        self._json({"ok": report.get("status") in (sr.OK, sr.PARTIAL, sr.AWAIT),
                    "is_release": True,
                    "executed": report.get("status") != sr.AWAIT,
                    "needs_confirmation": report.get("status") == sr.AWAIT,
                    "active_project_note": note, "intent": intent, **report})

    def _api_smart_release_confirm(self) -> None:
        self._api_smart_release_chat()   # same engine, explicit confirm endpoint

    def _api_smart_release_prepare(self) -> None:
        """Read-only proposal for a project's Git/GitHub readiness (no writes)."""
        from core import project_git
        data = self._read_json() or {}
        project, note = self._chat_project(data, str(data.get("text") or ""))
        if not project:
            self._error(400, note or "پروژه مشخص نشد")
            return
        self._json({"ok": True, "project": project, "active_project_note": note,
                    "state": project_git.repo_state(project),
                    "proposal": project_git.proposal(project,
                                                     str(data.get("session_id") or ""))})

    def _api_smart_release_repo(self, q: dict) -> None:
        from core import project_git
        project, note = self._chat_project(q, "")
        if not project:
            self._error(400, note or "پروژه مشخص نشد")
            return
        self._json({"ok": True, "project": project, "active_project_note": note,
                    **project_git.repo_state(project)})

    def _api_smart_release_last(self, q: dict) -> None:
        from core import smart_release as sr
        project = str(q.get("project") or "") or None
        r = sr.last(project)
        self._json({"ok": True, "report": r})

    def _api_smart_release_reports(self, q: dict) -> None:
        from core import smart_release as sr
        project = str(q.get("project") or "") or None
        self._json({"ok": True, "reports": sr.list_reports(project)})

    def _api_android_download(self, q: dict) -> None:
        """دانلود امن خروجی‌های موتور اندروید — فقط داخل exports/android-builds/<project>/."""
        from core import release_center
        project = q.get("project", "")
        release_id = q.get("release_id", "")
        artifact = q.get("artifact", "apk")
        if not _Name.match(project) or not release_id:
            self._error(400, "پروژه/نسخه نامعتبر")
            return
        try:
            rel = release_center.get_release(project, release_id)
        except KeyError as exc:
            self._error(404, str(exc))
            return
        want = "apk" if artifact == "apk" else "aab" if artifact == "aab" else ""
        if not want or want not in rel.get("artifacts", {}):
            self._error(404, "خروجی موردنظر در این نسخه نیست")
            return
        f = Path(rel["artifacts"][want]["path"])
        # path traversal guard: فایل باید دقیقاً داخل پوشهٔ نسخهٔ همان پروژه باشد
        base = (EXPORTS_DIR / "android-builds" / project).resolve()
        try:
            resolved = f.resolve()
        except OSError:
            self._error(404, "فایل یافت نشد")
            return
        if base != resolved and base not in resolved.parents:
            self._error(403, "مسیر خارج از محدودهٔ مجاز است")
            return
        if not resolved.is_file():
            self._error(404, "فایل یافت نشد")
            return
        body = resolved.read_bytes()
        ext = resolved.suffix or ".bin"
        fname = f"{project}-{release_id}{ext}"
        from urllib.parse import quote
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition",
                         f"attachment; filename*=UTF-8''{quote(fname)}")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _api_factory_types(self, q: dict) -> None:
        # R4-a: single source of truth — project types come from
        # core.config.PROJECT_TYPE_SPECS; dashboards fetch this instead of
        # keeping hardcoded copies. Same panel gate as every other API.
        from core.config import PROJECT_TYPE_SPECS
        self._json({"ok": True, "types": [
            {"id": tid,
             "label": spec.get("panel_label") or spec.get("engine_label", tid),
             "contract": bool(spec.get("contract")),
             "scaffold": bool(spec.get("scaffold"))}
            for tid, spec in PROJECT_TYPE_SPECS.items()]})

    def _api_factory_check(self, q: dict) -> None:
        from core import checker
        project = q.get("project", "")
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        d = _paths.project_dir(project)
        res = checker.check(d)
        res["project"] = project
        self._json({"ok": True, "result": res})

    def _api_factory_quality(self, q: dict) -> None:
        # AF-15 — honest quality report (definite errors / warnings /
        # real tests) with final status PASS / FAIL / NEEDS_REVIEW.
        from core import quality
        project = q.get("project", "")
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        boot = str(q.get("boot", "1")) not in ("0", "false", "no")
        try:
            res = quality.evaluate(project, boot=boot)
        except Exception as exc:  # noqa: BLE001 — honest failure, no fake pass
            self._error(500, f"ارزیابی کیفیت ناموفق: {exc}")
            return
        self._json({"ok": True, "result": res})

    def _api_quality_gate_view(self, q: dict) -> None:
        # AF NEXT P5 — read the last persisted Quality Gate report.
        project = q.get("project", "")
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        p = _paths.project_dir(project) / "quality" / "quality-report.json"
        if not p.is_file():
            self._json({"ok": True, "result": None,
                        "note": "هیچ ارزیابی کیفیت ذخیرهای وجود ندارد"})
            return
        try:
            self._json({"ok": True, "result": json.loads(
                p.read_text(encoding="utf-8"))})
        except (OSError, ValueError) as exc:
            self._error(500, "گزارش کیفیت نامعتبر: %s" % exc)

    def _api_quality_gate(self) -> None:
        # AF NEXT P5 — run the REAL Quality Gate through governed tools.
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        project = str(data.get("project", "")).strip()
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        engine = str(data.get("engine") or "").strip()
        do_build = str(data.get("build", "1")) not in ("0", "false", "no")
        from core import orchestrator
        try:
            res = orchestrator.quality_gate(project, engine, do_build=do_build)
        except Exception as exc:  # noqa: BLE001 — honest failure
            self._error(500, "ارزیابی کیفیت ناموفق: %s" % exc)
            return
        self._json({"ok": True, "result": res})

    def _api_workflow_state(self, q: dict) -> None:
        from core import workflow
        project = q.get("project", "")
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        st = workflow.state(project)
        self._json({"ok": True, "result": st,
                    "note": "" if st else "no-workflow-for-project"})

    def _api_workflow_artifact(self, q: dict) -> None:
        project = q.get("project", "")
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        rel = str(q.get("path") or "")
        try:
            p = _resolve_project_path(project, rel)
            _assert_not_secret(p, rel)
        except Exception:  # noqa: BLE001 — fail closed on path policy
            self._error(400, "مسیر Artifact رد شد")
            return
        if not p.is_file():
            self._error(404, "Artifact یافت نشد")
            return
        types = {".svg": "image/svg+xml", ".wav": "audio/wav",
                 ".png": "image/png", ".jpg": "image/jpeg",
                 ".md": "text/markdown; charset=utf-8",
                 ".html": "text/html; charset=utf-8",
                 ".py": "text/plain; charset=utf-8",
                 ".json": "application/json; charset=utf-8"}
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", types.get(p.suffix.lower(),
                                                   "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _api_workflow_events(self, q: dict) -> None:
        from core import workflow
        project = q.get("project", "")
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        last = -1
        try:
            for _ in range(150):          # bounded, like interactions SSE
                st = workflow.state(project)
                if st is None:
                    break
                if st.get("rev") != last:
                    last = st.get("rev")
                    frame = json.dumps(st, ensure_ascii=False).replace("\n", " ")
                    self.wfile.write(("data: " + frame + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                time.sleep(2)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _workflow_result(self, result: dict) -> None:
        ok = result.get("status") in ("PASS", "ok")
        self._json({"ok": ok, "result": result})

    def _api_workflow_start(self) -> None:
        from core import workflow
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        project = str(data.get("project", "")).strip()
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        self._workflow_result(workflow.create(
            project, str(data.get("product_type") or ""),
            str(data.get("goal") or "")))

    def _api_workflow_run(self) -> None:
        from core import workflow
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        project = str(data.get("project", "")).strip()
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        self._workflow_result(workflow.run_stage(
            project, str(data.get("stage") or ""),
            str(data.get("token") or ""), goal=str(data.get("goal") or "")))

    def _api_workflow_approve(self) -> None:
        from core import workflow
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            version = int(data.get("version"))
        except (TypeError, ValueError):
            self._error(400, "invalid version")
            return
        self._workflow_result(workflow.approve(
            str(data.get("project") or ""), str(data.get("stage") or ""),
            version, str(data.get("approval_id") or "")))

    def _api_workflow_reject(self) -> None:
        from core import workflow
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        self._workflow_result(workflow.reject(
            str(data.get("project") or ""), str(data.get("stage") or ""),
            str(data.get("note") or "")))

    def _api_workflow_revise(self) -> None:
        from core import workflow
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        self._workflow_result(workflow.revise(
            str(data.get("project") or ""), str(data.get("stage") or ""),
            str(data.get("note") or "")))

    def _api_workflow_retry(self) -> None:
        from core import workflow
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        self._workflow_result(workflow.retry_failed(
            str(data.get("project") or ""), str(data.get("stage") or ""),
            str(data.get("note") or "")))

    def _api_workflow_goal(self) -> None:
        from core import workflow
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        self._workflow_result(workflow.set_goal(
            str(data.get("project") or ""), str(data.get("goal") or "")))

    def _api_workflow_ask(self) -> None:
        from core import workflow
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        self._workflow_result(workflow.ask(
            str(data.get("project") or ""), str(data.get("stage") or ""),
            str(data.get("question") or ""), list(data.get("options") or []),
            bool(data.get("allow_other")), str(data.get("question_id") or "")))

    def _api_workflow_answer(self) -> None:
        from core import workflow
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        self._workflow_result(workflow.answer(
            str(data.get("project") or ""), str(data.get("stage") or ""),
            str(data.get("answer") or ""), str(data.get("question_id") or "")))

    def _api_factory_autofix(self) -> None:
        # AF-15 — ONE controlled fix cycle: snapshot → scoped model fix →
        # scope verify → re-test → rollback unless fully green.
        from core import autofix
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        project = str(data.get("project", "")).strip()
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        boot = str(data.get("boot", "1")) not in ("0", "false", "no")
        agent, aerr = self._valid_agent_or_none(data.get("agent"))
        if aerr:
            self._error(aerr[0], aerr[1])
            return
        try:
            res = autofix.run(project, agent=agent, boot=boot)
        except Exception as exc:  # noqa: BLE001 — honest failure
            log.error("autofix failed for %s: %s", project, exc)
            self._error(500, f"اصلاح خودکار ناموفق: {exc}")
            return
        self._json({"ok": True, "result": res})

    def _api_factory_fix(self) -> None:
        # P6-1: fix loop now covers static errors (checker) AND functional
        # contract errors (typetests). The functional phase is max ONE
        # controlled round followed by an honest re-test — errors are sent
        # to the model in the exact {file, kind, message} shape.
        from core import checker, factory, exporter, typetests
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        project = str(data.get("project", "")).strip()
        # AF-06.2: optional agent selection — validated against the real
        # registry (exists + enabled); absent = previous behavior
        agent, aerr = self._valid_agent_or_none(data.get("agent"))
        if aerr:
            self._error(aerr[0], aerr[1])
            return
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        attempts = data.get("attempts", 2)
        try:
            attempts = max(1, min(int(attempts), FIX_MAX_ATTEMPTS))
        except (TypeError, ValueError):
            attempts = 2
        d = _paths.project_dir(project)
        cycles = []
        res = checker.check(d)
        fixed = False
        attempt = 0
        backup_path = None
        if res["ok"]:
            cycles.append({"attempt": 0, "note": "خطای استاتیک یافت نشد"})
        else:
            backup_path = exporter.backup(project).get("path")
            for attempt in range(1, attempts + 1):
                req = factory.fix_request(project, res["errors"], attempt,
                                          agent=agent)
                done = factory.wait_done(req["session_id"], timeout=240,
                                         since_message_id=req["message_id"])
                cycles.append({"attempt": attempt, "wait_ok": done,
                               "errors_before": len(res["errors"])})
                if not done:
                    cycles[-1]["note"] = "مدل در ۲۴۰ ثانیه پاسخ نداد"
                    break
                res = checker.check(d)
                cycles[-1]["result_ok"] = res["ok"]
                if res["ok"]:
                    fixed = True
                    break
        # ---- functional (contract) phase: max ONE controlled round ----
        # Only booted when statics are clean; a skipped phase is reported
        # honestly, never presented as green.
        functional = {}
        if not res["ok"]:
            functional = {"fixed": False, "before": None,
                          "note": "فاز عملکردی اجرا نشد — ابتدا خطای "
                                  "استاتیک باید رفع شود"}
            before = None
        else:
            try:
                before = typetests.run(project)
            except (RuntimeError, OcError, OSError) as exc:
                before = {"ran": False, "no_coverage": True,
                          "note": f"تست قرارداد اجرا نشد: {exc}",
                          "errors": []}
            functional["before"] = before
            if before.get("ran") and before.get("ok"):
                functional["fixed"] = True
            elif before.get("ran"):
                errs = before.get("errors", [])
                if not backup_path:
                    backup_path = exporter.backup(project).get("path")
                req = factory.fix_request(project, errs, attempts + 1,
                                          agent=agent)
                done = factory.wait_done(req["session_id"], timeout=240,
                                         since_message_id=req["message_id"])
                cycles.append({"attempt": "functional", "wait_ok": done,
                               "errors_before": len(errs)})
                if not done:
                    cycles[-1]["note"] = "مدل در ۲۴۰ ثانیه پاسخ نداد"
                else:
                    after = typetests.run(project)
                    functional["after"] = after
                    cycles[-1]["result_ok"] = bool(after.get("ok"))
                    functional["fixed"] = bool(
                        after.get("ran") and after.get("ok"))
                    if functional["fixed"]:
                        res = checker.check(d)   # guard: statics intact
            else:
                functional["note"] = ("بدون پوشش تست عملکردی — "
                                      + (before.get("note") or ""))
        if before is None:
            note = functional.get("note", "")
        elif not before.get("ran"):
            note = (functional.get("note")
                    or "بدون پوشش تست عملکردی")
        elif functional.get("fixed"):
            note = "تست عملکردی سبز است"
        else:
            note = "خطای عملکردی پس از یک دور اصلاح باقی است"
        factory.append_history(
            project, "auto-fix",
            f"{attempt} cycles, fixed={fixed}, "
            f"functional={functional.get('fixed')}")
        factory.touch(project, "auto-fix")
        log.info("factory fix: %s cycles=%s fixed=%s functional=%s",
                 project, len(cycles), fixed, functional.get("fixed"))
        self._json({"ok": True, "fixed": fixed,
                    "functional": functional, "note": note,
                    "cycles": cycles, "result": res,
                    "backup": backup_path})

    def _api_factory_runs(self) -> None:
        from core import runner
        out = []
        for p in sorted(x.name for x in PROJECTS_DIR.iterdir()
                        if x.is_dir() and not x.name.startswith(".")):
            try:
                out.append({"name": p, **runner.status(p)})
            except Exception as exc:  # noqa: BLE001
                out.append({"name": p, "running": False, "error": str(exc)})
        self._json({"ok": True, "runs": out})

    def _api_factory_run_start(self) -> None:
        from core import runner
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        project = str(data.get("project", "")).strip()
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        try:
            res = runner.start(project)
        except (RuntimeError, OcError) as exc:
            self._error(409, str(exc))
            return
        log.info("factory run: %s -> %s", project, res)
        self._json({"ok": True, **res})

    def _api_factory_run_stop(self) -> None:
        from core import runner
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        project = str(data.get("project", "")).strip()
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        self._json({"ok": True, **runner.stop(project)})

    def _api_factory_runlog(self, q: dict) -> None:
        from core import runner
        project = q.get("project", "")
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        try:
            lines = max(1, min(int(q.get("lines", "80")), 400))
        except ValueError:
            lines = 80
        self._json({"ok": True, "logs": runner.runlog(project, lines)})

    def _api_factory_history(self, q: dict) -> None:
        from core import factory
        project = q.get("project", "")
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        self._json({"ok": True, "history": factory.history_of(project)})

    def _api_factory_export_list_or_download(self, q: dict) -> None:
        from core import exporter
        project = q.get("project", "")
        if project and q.get("download") == "1":
            if not _Name.match(project):
                self._error(400, "پروژه نامعتبر")
                return
            path = Path(q.get("path", ""))
            # the path must be exactly an export of this project
            exports_dir = EXPORTS_DIR.resolve()
            if path.parent != exports_dir or not path.is_file() \
               or not path.name.startswith(project + "-") or path.suffix != ".zip":
                self._error(403, "فایل خروجی مجاز نیست")
                return
            body = path.read_bytes()
            # RFC 5987/6266: HTTP headers are latin-1; a Persian project name
            # in the ZIP filename must never go raw into those headers.
            #   - filename="..."   : ASCII-safe fallback (some old clients)
            #   - filename*=UTF-8''…: percent-encoded real name (RFC 5987)
            fallback = "".join(c if c.isascii() and (c.isalnum() or c in "._-")
                               else "-" for c in path.name)
            fallback = fallback.strip("-") or "export.zip"
            quoted = quote(path.name, safe="")
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition",
                             f"attachment; filename=\"{fallback}\"; "
                             f"filename*=UTF-8''{quoted}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass
            return
        self._json({"ok": True, "exports": exporter.list_exports()})

    def _api_deploy_dests_list(self) -> None:
        from core import deploy
        self._json({"ok": True, "dests": deploy.list_dests()})

    def _api_deploy_history(self, q: dict) -> None:
        from core import deploy
        self._json({"ok": True, "history": deploy.history(
            (q.get("project") or "").strip(),
            (q.get("dest") or "").strip())})

    def _api_deploy_dests_save(self) -> None:
        from core import deploy
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            res = deploy.save_dest(data)
        except deploy.DeployError as exc:
            self._error(exc.status, str(exc))
            return
        self._json({"ok": True, **res}, status=201)

    def _api_deploy_preflight(self) -> None:
        from core import deploy
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            res = deploy.preflight(str(data.get("project", "")).strip(),
                                   str(data.get("dest", "")).strip(),
                                   (str(data.get("release")).strip()
                                    if data.get("release") else None))
        except deploy.DeployError as exc:
            self._error(exc.status, str(exc))
            return
        self._json({"ok": True, **res})

    def _api_deploy_start(self) -> None:
        from core import deploy
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            rec = deploy.deploy(str(data.get("project", "")).strip(),
                                str(data.get("dest", "")).strip(),
                                (str(data.get("release")).strip()
                                 if data.get("release") else None),
                                confirm=bool(data.get("confirm")))
        except deploy.DeployError as exc:
            self._error(exc.status, str(exc))
            return
        self._json({"ok": rec.get("status") == "success", "record": rec},
                   status=201 if rec.get("status") == "success" else 502)

    def _api_deploy_rollback(self) -> None:
        from core import deploy
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            rec = deploy.rollback(str(data.get("project", "")).strip(),
                                  str(data.get("dest", "")).strip(),
                                  confirm=bool(data.get("confirm")))
        except deploy.DeployError as exc:
            self._error(exc.status, str(exc))
            return
        self._json({"ok": rec.get("status") == "success", "record": rec})

    def _api_deploy_stop(self) -> None:
        from core import deploy
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        try:
            rec = deploy.stop(str(data.get("project", "")).strip(),
                              str(data.get("dest", "")).strip())
        except deploy.DeployError as exc:
            self._error(exc.status, str(exc))
            return
        self._json({"ok": rec.get("status") == "success", "record": rec})

    def _api_factory_release_create(self) -> None:
        # R3: clean standalone release ZIP — technology detection, generated
        # README/run scripts, release.json manifest, full structural verify.
        # AF-07: the release passes the security gate FIRST; a confirmed
        # critical finding stops the release (409) and a gate that cannot
        # run stops it too (fail-closed — no unscanned release ever ships).
        from core import release, security_gate
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        project = str(data.get("project", "")).strip()
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        try:
            gate = security_gate.scan(project, mode="release")
        except Exception as exc:  # noqa: BLE001 — fail closed, never unscanned
            log.error("security gate crashed on %s: %s", project, exc)
            self._error(500, "دروازهٔ امنیت قابل اجرا نشد؛ انتشار متوقف شد")
            return
        if gate["blocked"]:
            self._error(409, security_gate.summary(gate), gate["critical"])
            return
        res = release.build_release(project)
        res["security_gate"] = {"ok": True,
                                "warnings": gate["warnings"],
                                "files_scanned": gate["files_scanned"]}
        self._json({"ok": True, **res}, status=201)

    def _api_factory_export_create(self) -> None:
        # AF-07: same security gate as release — export is also a release
        # path (the ZIP leaves the factory). Confirmed critical findings
        # block it; a crashed gate blocks it (fail closed).
        from core import exporter, security_gate
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        project = str(data.get("project", "")).strip()
        to_dl = bool(data.get("to_downloads", False))
        if not _Name.match(project):
            self._error(400, "پروژه نامعتبر")
            return
        try:
            gate = security_gate.scan(project, mode="export")
        except Exception as exc:  # noqa: BLE001 — fail closed, never unscanned
            log.error("security gate crashed on %s: %s", project, exc)
            self._error(500, "دروازهٔ امنیت قابل اجرا نشد؛ انتشار متوقف شد")
            return
        if gate["blocked"]:
            self._error(409, security_gate.summary(gate), gate["critical"])
            return
        res = exporter.export(project)
        res["security_gate"] = {"ok": True,
                                "warnings": gate["warnings"],
                                "files_scanned": gate["files_scanned"]}
        if to_dl:
            res["downloads"] = exporter.to_downloads(res["path"])
        self._json({"ok": True, **res})

    # ---- concrete API implementations -----------------------------------

    def _api_projects_list(self) -> None:
        items = []
        for p in sorted(PROJECTS_DIR.iterdir()):
            if p.is_dir() and not p.name.startswith("."):
                files = [f.name for f in p.iterdir() if f.is_file()]
                stat = p.stat()
                items.append({
                    "name": p.name,
                    "files": len(files),
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                })
        self._json({"ok": True, "projects": items})

    def _api_projects_create(self) -> None:
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body or body too large")
            return
        name = str(data.get("name", "")).strip()
        description = str(data.get("description", "")).strip()
        if not _Name.match(name):
            self._error(400, "نام پروژه نامعتبر است (حروف، اعداد، خط تیره؛ حداکثر ۶۴ کاراکتر)")
            return
        project_dir = _resolve_safe(PROJECTS_DIR, name)
        if project_dir.exists():
            raise FileExistsError("پروژه‌ای با این نام از قبل وجود دارد")
        project_dir.mkdir()
        (project_dir / "README.md").write_text(
            f"# {name}\n\n{description or ('پروژه ساخته‌شده در ' + APP_NAME)}\n",
            encoding="utf-8",
        )
        log.info("project created: %s", name)
        self._json({"ok": True, "name": name}, 201)

    def _api_files_list(self, q: dict) -> None:
        project = q.get("project", "")
        if not _Name.match(project):
            raise PermissionError("invalid project name")
        project_dir = _resolve_safe(PROJECTS_DIR, project)
        if not project_dir.is_dir():
            raise FileNotFoundError("project does not exist")
        entries = []
        for f in sorted(project_dir.rglob("*")):
            if f.is_file():
                rel = f.relative_to(project_dir)
                if _is_secret(rel):
                    continue           # P5: private files are not listed
                entries.append({
                    "path": rel.as_posix(),
                    "size_bytes": f.stat().st_size,
                    "editable": f.suffix.lower() in TEXT_EXTENSIONS,
                })
        self._json({"ok": True, "project": project, "files": entries})

    def _api_file_read(self, q: dict) -> None:
        rel = q.get("path", "")
        path = _resolve_project_path(q.get("project", ""), rel)
        _assert_not_secret(path, rel)
        if not path.is_file():
            raise FileNotFoundError("file not found")
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            self._json({"ok": True, "path": q.get("path"), "binary": True})
            return
        content = path.read_text(encoding="utf-8", errors="replace")
        self._json({"ok": True, "path": q.get("path"), "content": content})

    def _api_file_write(self) -> None:
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body or body too large")
            return
        project = str(data.get("project", ""))
        rel = str(data.get("path", ""))
        content = data.get("content", "")
        if not isinstance(content, str):
            self._error(400, "content must be a string")
            return
        path = _resolve_project_path(project, rel)
        _assert_not_secret(path, rel)
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            self._error(403, "file type not allowed for editing")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        log.info("file written: %s/%s", project, rel)
        self._json({"ok": True, "saved": rel})

    def _api_file_rename(self) -> None:
        """P5: rename/move a text file *within* the project (no cross-project
        moves, target must be text, target must not pre-exist)."""
        data = self._read_json()
        if data is None:
            self._error(400, "invalid JSON body")
            return
        project = str(data.get("project", ""))
        src_rel = str(data.get("path", "")).strip()
        dst_rel = str(data.get("new_path", "")).strip()
        if not src_rel or not dst_rel:
            self._error(400, "منبع و مقصد لازم است")
            return
        src = _resolve_project_path(project, src_rel)
        dst = _resolve_project_path(project, dst_rel)
        _assert_not_secret(src, src_rel)
        _assert_not_secret(dst, dst_rel)
        if dst.suffix.lower() not in TEXT_EXTENSIONS:
            self._error(403, "نوع فایل مقصد برای ویرایش مجاز نیست")
            return
        if not src.is_file():
            raise FileNotFoundError("file not found")
        if dst.exists():
            self._error(409, "فایلی با نام مقصد از قبل وجود دارد")
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        log.info("file renamed: %s: %s -> %s", project, src_rel, dst_rel)
        self._json({"ok": True, "renamed": dst_rel, "from": src_rel})

    def _api_file_delete(self, q: dict) -> None:
        path = _resolve_project_path(q.get("project", ""), q.get("path", ""))
        _assert_not_secret(path, q.get("path", ""))
        if path.is_dir():
            self._error(400, "refusing to delete a directory")
            return
        if not path.is_file():
            raise FileNotFoundError("file not found")
        path.unlink()
        log.info("file deleted: %s/%s", q.get("project"), q.get("path"))
        self._json({"ok": True, "deleted": q.get("path"), "project": q.get("project")})

    def _api_logs(self, q: dict) -> None:
        try:
            lines = min(max(int(q.get("lines", "50")), 1), 500)
        except ValueError:
            lines = 50
        which = {"server": LOG_FILE, "error": ERROR_LOG_FILE}.get(q.get("which", "server"), LOG_FILE)
        try:
            text = "\n".join(which.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
        except FileNotFoundError:
            text = "لاگ هنوز خالی است"
        self._json({"ok": True, "file": which.name, "logs": text})


def main() -> int:
    if PID_FILE.exists() and _server_is_ours():
        print("server already running")
        return 1
    httpd = ThreadingHTTPServer((f"127.0.0.1", PORT), Handler)
    httpd.daemon_threads = True
    _write_pid()
    # AF-56 — one controlled expiry watcher for unanswered permissions.
    # Existing (legacy) requests are snapshotted and never auto-answered.
    from core.model_gateway import gateway
    try:
        gateway().start_permission_watch()
    except Exception as exc:  # noqa: BLE001 — never block the panel on it
        log.warning("permission watcher could not start: %s", exc)
    log.info("%s %s started on http://127.0.0.1:%s", APP_NAME, APP_VERSION, PORT)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        _remove_pid()
        log.info("server stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
