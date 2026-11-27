"""Factory runner — safe, controlled execution of generated projects.

Rules (non-negotiable):
  * No shell is ever involved: fixed argv, interpreters only
    (python / node), created from project *facts* — never from a raw
    request string.
  * The command targets a file that must resolve inside the project dir.
  * Every process is registered so only the runner can stop it.
  * Ports come from RUN_PORT_RANGE; conflicts are detected by bind probing.
  * Environment passed to children is stripped of all AI Factory secrets
    (OPENCODE_SERVER_PASSWORD etc.) and the child gets AI_FACTORY_PORT.
"""

import hashlib
import json
import logging
import os
import re
import signal
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import core.config as config
from core import paths

log = logging.getLogger("ai-factory.runner")

_lock = threading.Lock()
_reg: dict[str, dict] = {}   # project -> proc record

_PY = Path("/data/data/com.termux/files/usr/bin/python")
_NODE = Path("/data/data/com.termux/files/usr/bin/node")
_PHP = Path("/data/data/com.termux/files/usr/bin/php")
_SH = {p for p in (_PY, _NODE, _PHP) if p.exists()}

_ENV_BLACKLIST = ("OPENCODE_SERVER_PASSWORD", "OPENCODE_SESSION_ID",
                  "OPENCODE", "OPENCODE_TERMINAL", "AI_AGENT")

KIND_LABEL = {"static-web": "وب استاتیک",
              "python": "فارسی/سرویس پایتون",
              "node": "سرویس Node.js",
              "canonical": "پیشنمایش رسمی معلم من",
              "php": "سرویس PHP"}

# MOALLEM A7 — Preview رسمی Student App دقیقاً یک آدرس و یک سرویس دارد:
#   http://127.0.0.1:8610/  ← projects/teacher-android/assets/www/
# سرویسدهندهٔ واقعی، بکاند دستیار است (A23 همان منبع canonical را سرو میکند).
# Factory Run نباید برای این پروژه پورت دیگری (8611...) بگیرد و نسخهٔ موازی بسازد.
_CANONICAL_PORT = 8610
_CANONICAL_PROJECT = "teacher-android"


def _canonical_paths() -> tuple[Path, Path] | None:
    if not paths.project_dir(_CANONICAL_PROJECT).is_dir():
        return None
    assistant = paths.project_dir("دستیار")
    source = paths.project_dir(_CANONICAL_PROJECT) / "assets" / "www"
    if not (assistant / "server.py").is_file() or not (source / "app.js").is_file():
        return None
    return assistant.resolve(), source.resolve()


def _canonical_server_pid(assistant: Path) -> int | None:
    """یافتن سرور بکاند دستیارwhich روی پورت رسمی 8610 در حال اجراست."""
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
            env = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        except (OSError, PermissionError):
            continue
        if Path(cwd).resolve() != assistant or b"server.py" not in cmd:
            continue
        if not any(x in (b"AF_PORT=8610", b"AI_FACTORY_PORT=8610") for x in env):
            continue
        return pid
    return None


def _canonical_source_current(source: Path) -> bool:
    """Preview فقط زمانی معتبر است که دقیقاً همان سورس جاری را سرو کند."""
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{_CANONICAL_PORT}/app.js", timeout=2) as r:
            served = r.read()
    except Exception:                                       # noqa: BLE001
        return False
    return hashlib.sha256(served).digest() == hashlib.sha256(
        (source / "app.js").read_bytes()).digest()


# P7-4: port allocation is concurrency-safe — bind-probe + reservation under
# one lock, so two parallel runner starts (or two orchestrator workers) can
# never be handed the same preview port (8610–8659).
_port_lock = threading.Lock()
_port_reservations: dict[int, float] = {}      # port -> reserved_at
_PORT_RESERVATION_TTL = 45.0                   # auto-expire stale hand-outs


def _expire_port_reservations() -> None:
    now = time.time()
    for p, ts in list(_port_reservations.items()):
        if now - ts > _PORT_RESERVATION_TTL:
            _port_reservations.pop(p, None)


def _free_port() -> int:
    with _port_lock:
        _expire_port_reservations()
        for port in config.RUN_PORT_RANGE:
            if port in _port_reservations:
                continue
            s = socket.socket()
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", port))
                _port_reservations[port] = time.time()
                return port
            except OSError:
                continue
            finally:
                s.close()
    raise RuntimeError("پورت آزادی در محدوده پیش‌نمایش موجود نیست")


def _release_port(port: int | None) -> None:
    """Give a reserved port back before its TTL (runner stop / failed start)."""
    if not port:
        return
    with _port_lock:
        _port_reservations.pop(int(port), None)


def _project_proc_alive(pid: int, project_dir: Path) -> bool:
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
    except OSError:
        return False
    return (str(project_dir).encode() in cmd)


def _detect(project_dir: Path) -> dict:
    """Detect run command from project facts (never from user request)."""
    # node project: package.json with main
    pkg = project_dir / "package.json"
    if pkg.is_file():
        try:
            main = (json.loads(pkg.read_text(encoding="utf-8")).get("main") or "").strip()
        except ValueError:
            main = ""
        if main:
            entry = (project_dir / main)
            entry.resolve()
            # AF-13 fix: pre-existing crash — paths.PROJECTS_DIR never existed
            # (the node branch could never run; no node project existed).
            # Entry must live INSIDE this project directory.
            if str(entry.resolve()).startswith(str(Path(project_dir).resolve())):
                if entry.is_file():
                    return {"kind": "node", "cmd": [str(_NODE), str(entry.resolve())],
                            "preview": "internal-port"}
    # python service: server.py / main.py / app.py (single file)
    for cand in ("server.py", "main.py", "app.py"):
        f = (project_dir / cand)
        if f.is_file():
            return {"kind": "python", "cmd": [str(_PY), str(f.resolve())],
                    "preview": "internal-port"}
    # AF-47 — PHP first-class. A WordPress plugin usually has no standalone
    # entry; index.php/server.php (when present) is served by PHP's own
    # built-in server on the assigned port.
    if (project_dir / "index.php").is_file() or \
            (project_dir / "server.php").is_file():
        if _PHP.exists():
            return {"kind": "php", "cmd": None, "preview": "assigned-port"}
    # static web: index.html present
    if (project_dir / "index.html").is_file():
        return {"kind": "static-web", "cmd": None,   # filled at start()
                "preview": "assigned-port"}
    return {"kind": "runnable-not-found", "cmd": None, "preview": None}


def status(project: str) -> dict:
    d = paths.project_dir(project)
    # A7 — مسیر رسمی: وضعیت واقعی تک سرویس 8610، نه پورت تخصیصی تصادفی.
    cp = _canonical_paths() if project == _CANONICAL_PROJECT else None
    if cp:
        assistant, source = cp
        pid = _canonical_server_pid(assistant)
        if pid is not None:
            if not _canonical_source_current(source):
                return {"running": False, "preview": None,
                        "error": ("پیشنمایش 8610 سورس قدیمی سرو میکند؛ "
                                  "سرویس را متوقف و مجدداً Run کنید")}
            return {"running": True, "pid": pid, "port": _CANONICAL_PORT,
                    "kind": "canonical",
                    "kind_label": KIND_LABEL["canonical"],
                    "preview": f"http://127.0.0.1:{_CANONICAL_PORT}/",
                    "canonical": True, "external": True}
        if not _port_free_now(_CANONICAL_PORT):
            return {"running": False, "preview": None,
                    "error": ("پورت رسمی 8610 توسط سرویس دیگری اشغال است؛ "
                              "نسخهٔ قدیمی بهعنوان Preview نمایش داده نمیشود")}
        return {"running": False, "preview": None}
    with _lock:
        rec = _reg.get(project)
    if rec and rec["proc"].poll() is None and _project_proc_alive(rec["pid"], d):
        return {"running": True, "pid": rec["pid"], "port": rec["port"],
                "kind": rec["kind"], "kind_label": KIND_LABEL.get(rec["kind"], rec["kind"]),
                "preview": f"http://127.0.0.1:{rec['port']}",
                "started": rec["started"]}
    if rec:
        # exited; clear
        with _lock:
            _reg.pop(project, None)
    return {"running": False}


def start(project: str) -> dict:
    d = paths.project_dir(project)
    # A7 — Run فقط همان تک سرویس رسمی را میپذیرد؛ اجرای مجدد سرویس موازی جدید
    # ایجاد نمیکند، بلکه همان وضعیت 8610 را برمیگرداند.
    if project == _CANONICAL_PROJECT:
        cp = _canonical_paths()
        if cp is None:
            raise RuntimeError("منبع رسمی Preview معلم من یافت نشد")
        st = status(project)
        if st.get("running"):
            return st
        if st.get("error"):
            raise RuntimeError(st["error"])
        assistant, _ = cp
        config.RUN_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_path = config.RUN_LOGS_DIR / f"{project}.log"
        log_fp = open(log_path, "ab")
        from core import boundary
        env = boundary.clean_env({"AF_PORT": str(_CANONICAL_PORT),
                                  "AF_HOST": "127.0.0.1",
                                  "AI_FACTORY_PORT": str(_CANONICAL_PORT)})
        cmd = [str(_PY), str(assistant / "server.py")]
        proc = subprocess.Popen(cmd, cwd=str(assistant), env=env,
                                stdout=log_fp, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        rec = {"proc": proc, "pid": proc.pid, "port": _CANONICAL_PORT,
               "kind": "canonical", "cmd": cmd, "log_path": str(log_path),
               "started": datetime.now().isoformat(timespec="seconds")}
        with _lock:
            _reg[project] = rec
        if not _port_serving(_CANONICAL_PORT, wait=6.0):
            with _lock:
                _reg.pop(project, None)
            raise RuntimeError("سرویس Preview رسمی روی 8610 پاسخ نداد؛ لاگ را ببینید")
        return status(project)
    if status(project)["running"]:
        return status(project)
    det = _detect(d)
    if det["kind"] == "runnable-not-found":
        raise RuntimeError("این پروژه قابل اجرا نیست (نه index.html، نه server.py/main.py/app.py)")

    port = None
    cmd = det["cmd"]
    if det["preview"] == "assigned-port":
        port = _free_port()
        if det["kind"] == "php":
            cmd = [str(_PHP), "-S", "127.0.0.1:%d" % port,
                   "-t", str(d.resolve())]
        else:
            cmd = [str(_PY), "-m", "http.server", str(port),
                   "--bind", "127.0.0.1", "--directory", str(d.resolve())]
    else:
        port = _free_port()   # reserve for node/python service (best-effort)
        if det["kind"] == "node":
            pass  # many apps read PORT env; we set it below
    config.RUN_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = config.RUN_LOGS_DIR / f"{project}.log"
    log_fp = open(log_path, "ab")
    from core import boundary
    env = boundary.clean_env({"AI_FACTORY_PORT": str(port),
                              "PORT": str(port), "AI_FACTORY": "1"})
    proc = subprocess.Popen(cmd, cwd=str(d.resolve()), env=env,
                            stdout=log_fp, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    rec = {"proc": proc, "pid": proc.pid, "port": port, "kind": det["kind"],
           "cmd": cmd, "log_path": str(log_path),
           "started": datetime.now().isoformat(timespec="seconds")}
    with _lock:
        _reg[project] = rec
    log.info("factory run started: %s kind=%s pid=%s port=%s",
             project, det["kind"], proc.pid, port)
    # P6-5: real post-boot liveness probe on the assigned port + honest
    # diagnostics (log tail on failure, mismatch note when alive elsewhere)
    time.sleep(1.2)
    st = status(project)
    result = {**st,
              "preview": (f"http://127.0.0.1:{port}/"
                          if st.get("running") else None),
              "log_path": str(log_path)}
    if st.get("running"):
        if not _port_serving(port, wait=3.0):
            result["port_warning"] = (
                "پردازش زنده است اما روی پورت تخصیص‌یافته پاسخ نمی‌دهد — "
                "احتمالاً پورت دیگری را bind کرده؛ لاگ را ببینید")
    else:
        result["log_tail"] = runlog(project, 8)
        _release_port(port)          # P7-4: start failed — don't hold the port
    return result


def _port_serving(port: int, wait: float = 2.0) -> bool:
    """True once 127.0.0.1:port answers — real HTTP probe, bounded."""
    import urllib.request
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/", timeout=1.5) as r:
                return 200 <= r.status < 500
        except Exception:                                  # noqa: BLE001
            time.sleep(0.4)
    return False


def stop(project: str) -> dict:
    with _lock:
        rec = _reg.pop(project, None)
    if rec is None:
        # A7 — سرویس رسمی 8610 ممکن است توسط Factory adoption ثبت نشده باشد
        # (مثلاً پیش از راهاندازی Factory آغاز شده). Stop همانرا متوقف میکند.
        cp = _canonical_paths() if project == _CANONICAL_PROJECT else None
        if cp:
            pid = _canonical_server_pid(cp[0])
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError as exc:
                    return {"stopped": False, "note": str(exc)}
                for _ in range(10):
                    if _port_free_now(_CANONICAL_PORT):
                        break
                    time.sleep(0.4)
                return {"stopped": True, "pid": pid,
                        "port": _CANONICAL_PORT,
                        "port_free": _port_free_now(_CANONICAL_PORT)}
        return {"stopped": False, "note": "در حال اجرا نیست"}
    proc = rec["proc"]
    try:
        proc.terminate()
        for _ in range(8):
            if proc.poll() is not None:
                break
            time.sleep(0.4)
        if proc.poll() is None:
            proc.kill()
        log.info("factory run stopped: %s (pid=%s)", project, rec["pid"])
        # P6-5: verify the assigned port is really released — small retry
        # window because a killed process releases its socket a beat later.
        port_free = False
        for _ in range(5):
            if _port_free_now(rec.get("port") or 0):
                port_free = True
                break
            time.sleep(0.4)
        # P7-4: the reservation dies with the run — port becomes reusable at once
        _release_port(rec.get("port"))
        return {"stopped": True, "port": rec.get("port"),
                "port_free": port_free,
                **({} if port_free else
                   {"note": "پردازش متوقف شد اما پورت هنوز اشغال است "
                            "(فرایند دیگری؟)"})}
    except OSError as exc:
        log.error("stop failed %s: %s", project, exc)
        return {"stopped": False, "note": str(exc)}


def _port_free_now(port: int) -> bool:
    if not port:
        return False
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # noqa: S104
    try:
        # SO_REUSEADDR: a TIME_WAIT residue is not a live listener; only a
        # genuinely bound port must fail this check.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def stop_all() -> None:
    with _lock:
        projs = list(_reg)
    for p in projs:
        stop(p)


def runlog(project: str, lines: int = 60) -> str:
    d = paths.project_dir(project)   # validation
    log_path = config.RUN_LOGS_DIR / f"{project}.log"
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        return "\n".join(text.splitlines()[-lines:])
    except FileNotFoundError:
        return "لاگ اجرای این پروژه هنوز خالی است."


# ---------------------------------------------------------------------------
def run_tests(project: str, timeout: float = 300.0) -> dict:
    """Run the project's own test suite (fixed argv, no shell, localhost).

    Fixed candidates: tests/test_api.py, tests/test_*.py (unittest).
    Output is truncated; secrets could never reach here because tests run
    in the project dir with a stripped environment.
    """
    d = paths.project_dir(project)
    # AF-13 — node project: package.json → node:test suite (real HTTP tests)
    if (d / "package.json").is_file():
        tdir = d / "tests"
        if not tdir.is_dir():
            return {"ran": False,
                    "note": "پوشهٔ tests یافت نشد (node — node --test)"}
        try:
            proc = subprocess.run(
                [str(_NODE), "--test"], capture_output=True, text=True,
                timeout=timeout, cwd=str(d.resolve()), shell=False,
                env={k: v for k, v in os.environ.items()
                     if k not in _ENV_BLACKLIST})
            out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            return {"ran": True, "exit": proc.returncode,
                    "ok": proc.returncode == 0,
                    "output": out[-4000:]}
        except subprocess.TimeoutExpired:
            return {"ran": True, "exit": -1, "ok": False,
                    "output": "timeout", "note": "آزمون node از مهلت گذشت"}
    # AF-47 — PHP projects: real `php -l` over every shipped PHP file.
    if (d / "index.php").is_file() or (d / "server.php").is_file() or \
            any(d.rglob("*.php")):
        php_files = sorted(f for f in d.rglob("*.php")
                           if "__pycache__" not in str(f))
        if not php_files:
            return {"ran": False, "note": "هیچ فایل PHP برای آزمون یافت نشد"}
        outs, ok = [], True
        for f in php_files[:120]:
            try:
                p = subprocess.run([str(_PHP), "-l", str(f)],
                                   capture_output=True, text=True, timeout=60,
                                   cwd=str(d.resolve()), shell=False,
                                   env={k: v for k, v in os.environ.items()
                                        if k not in _ENV_BLACKLIST})
                ok = ok and p.returncode == 0
                outs.append((p.stdout or p.stderr or "").strip())
            except (OSError, subprocess.SubprocessError) as exc:
                ok = False
                outs.append(str(exc))
        return {"ran": True, "ok": ok, "exit": 0 if ok else 1,
                "output": "\n".join(outs)[-4000:]}

    test_file = None
    for cand in ("tests/test_api.py", "tests/api_test.py"):
        f = (d / cand)
        if f.is_file():
            test_file = f
            break
    if test_file is None:
        # discover any tests/test_*.py
        tdir = d / "tests"
        if tdir.is_dir():
            for f in sorted(tdir.glob("test_*.py")):
                test_file = f
                break
    if test_file is None:
        return {"ran": False, "note": "فایل آزمون (tests/test_*.py) یافت نشد"}
    resolved = test_file.resolve()
    if not str(resolved).startswith(str(d.resolve())):
        return {"ran": False, "note": "مسیر آزمون خارج از پروژه است"}
    try:
        proc = subprocess.run(
            [str(_PY), str(resolved)], capture_output=True, text=True,
            timeout=timeout, cwd=str(d.resolve()), shell=False,
            env={**{k: v for k, v in os.environ.items()
                    if k not in _ENV_BLACKLIST},
                 # tests may import project modules (import server …);
                 # run from the project root standard requires it.
                 "PYTHONPATH": str(d.resolve())})
        out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        return {"ran": True, "exit": proc.returncode,
                "ok": proc.returncode == 0,
                "output": out[-4000:]}
    except subprocess.TimeoutExpired:
        return {"ran": True, "exit": -1, "ok": False,
                "output": "آزمون به سقف زمانی %d ثانیه رسید" % int(timeout)}
    except OSError as exc:
        return {"ran": False, "note": "اجرای آزمون ناموفق: %s" % exc}


def install_deps(project: str, timeout: float = 300.0,
                 target: str | None = None) -> dict:
    """pip install -r requirements.txt if present and non-trivial.

    Fixed argv, no shell, logged. Skipped instantly for stdlib-only
    projects (empty/comment-only requirements.txt).

    AF-19 (honesty fixes, evidence-based):
      * success is NEVER claimed from the return code alone — after pip
        exits 0, EVERY requirement line is re-probed with the real
        fresh-interpreter import check; unprovable lines fail the call;
      * a failed install reports the REAL package name(s) from pip's
        output and an explicit offline hint when the network/source is
        unreachable;
      * `target` (isolated envs/tests only) adds --no-index --target so
        a LOCAL wheel installs offline without touching site-packages.
    """
    d = paths.project_dir(project)
    req = d / "requirements.txt"
    if not req.is_file():
        return {"installed": False, "note": "requirements.txt وجود ندارد"}
    lines = [ln.strip() for ln in req.read_text(encoding="utf-8",
                                                errors="replace").splitlines()
             if ln.strip() and not ln.strip().startswith(("#", "//"))]
    if not lines:
        return {"installed": False,
                "note": "requirements.txt خالی است (ستاندرد لایب‌ری — نصبی لازم نیست)"}
    argv = [str(_PY), "-m", "pip", "install", "--no-input", "-r",
            str(req.resolve())]
    if target:
        argv += ["--no-index", "--target", str(target)]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=timeout, cwd=str(d.resolve()), shell=False,
            env={k: v for k, v in os.environ.items()
                 if k not in _ENV_BLACKLIST})
        out = (proc.stderr or proc.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return {"installed": False, "verified": False,
                "note": "نصب به سقف زمانی رسید"}
    except OSError as exc:
        return {"installed": False, "verified": False,
                "note": str(exc)[:300]}
    if proc.returncode != 0:
        pkgs, offline = _parse_fail(out)
        note = "نصب pip ناموفق بود"
        if pkgs:
            note += " — بسته‌ها: " + ", ".join(pkgs)
        if offline:
            note += " — منبع نصب در دسترس نیست (آفلاین/شبکه)"
        return {"installed": False, "exit": proc.returncode,
                "output": out[-3000:], "failed_packages": pkgs,
                "offline": offline, "verified": False, "note": note}
    # ---- AF-19: exit 0 is NOT success until every line truly imports ----
    from core import deps as depsmod
    unverified = []
    for ln in lines:
        name, _spec = depsmod.split_req(ln)
        base = name.replace("-", "_").lower()
        cands = {base, name.lower(), base.split("_")[0]}
        if not any(depsmod.importable(c, d, extra_path=target)
                   for c in cands):
            unverified.append(ln)
    if unverified:
        return {"installed": False, "exit": proc.returncode,
                "verified": False, "unverified": unverified,
                "output": out[-3000:],
                "note": "pip کد خروج ۰ داد اما این وابستگی‌ها واردشدنی "
                        "نیستند — «نصب موفق» ثبت نمی‌شود: "
                        + ", ".join(unverified)}
    return {"installed": True, "exit": proc.returncode, "verified": True,
            "output": out[-3000:],
            "note": "نصب و واردشدنی بودن همهٔ وابستگی‌ها با پروب واقعی تأیید شد"}


_DIST_FAIL_RE = re.compile(
    r"No matching distribution found for ([A-Za-z0-9_.\-]+)")
_OFFLINE_HINTS = ("failed to resolve", "retrying", "connection",
                  "timed out", "network is unreachable",
                  "could not fetch url")


def _parse_fail(out: str) -> tuple[list[str], bool]:
    """AF-19 — extract the REAL failed package name(s) and offline
    evidence from actual pip output (no guessing from the return code)."""
    pkgs = sorted(set(_DIST_FAIL_RE.findall(out or "")))
    low = (out or "").lower()
    offline = any(h in low for h in _OFFLINE_HINTS)
    return pkgs, offline
