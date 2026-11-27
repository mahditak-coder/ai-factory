"""R4 — deployment engine: controlled, audited deploy of release packages.

Built ON TOP of the R3 release pipeline (core/release.py) — releases are the
ONLY deployable artifacts; technology detection/verification is reused, not
duplicated.

Destinations (data/deploy_dests.json):
  local  → independent directory on this device (default ~/deployments),
           own port from 8700-8739, own process — detached from the panel
  remote → Linux server over ssh/scp (key auth only; passwords unsupported)

Guarantees:
  * deploy/rollback require explicit confirm=true
  * only verified release ZIPs from exports/<project>-release-*.zip deploy
  * deployment steps + errors are recorded per record (data/deployments.json)
  * health check after start; URL/port recorded
  * rollback redeploys the previous successful release and proves it (hash)
  * global lock: one deploy operation at a time; per-dest guarded
  * secrets never stored: no password field is accepted, SSH key path is kept
    but only surfaced as has_key=true; key contents are never read/logged
"""

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import core.config as config
from core import release as releasemod
from core import paths

log = __import__("logging").getLogger("ai-factory.deploy")

DATA_DIR = config.PROJECT_ROOT / "data"
DESTS_FILE = DATA_DIR / "deploy_dests.json"
HISTORY_FILE = DATA_DIR / "deployments.json"
LOGS_DIR = config.PROJECT_ROOT / "logs" / "deploy"
LOCK_FILE = DATA_DIR / "deploy.lock"

LOCAL_ROOT = Path.home() / "deployments"          # outside the factory tree
DEFAULT_LOCAL_DEST = {"name": "local-prod", "kind": "local", "root": str(LOCAL_ROOT)}
PORT_RANGE = range(8700, 8740)                    # never 8580 / 4556 / 8610-8659
BUSY_LOCK_MAX_AGE = 600.0                         # steal stale lock after 10 min

_PROC_LOCK = threading.Lock()
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_SECRET_KEY_NAMES = {"password", "passwd", "secret", "token", "private_key",
                     "key_material", "passphrase"}


class DeployError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ── destinations ─────────────────────────────────────────────────────────────

def _load_dests() -> dict:
    if DESTS_FILE.is_file():
        try:
            return json.loads(DESTS_FILE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {}


def _save_dests(d: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DESTS_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1),
                          encoding="utf-8")


def _seed_default() -> None:
    d = _load_dests()
    if "local-prod" not in d:
        d["local-prod"] = dict(DEFAULT_LOCAL_DEST)
        _save_dests(d)


def get_dest(name: str) -> dict:
    dests = _load_dests()
    if name not in dests:
        raise DeployError("مقصد استقرار یافت نشد: %s" % name, 404)
    return dict(dests[name])


def list_dests() -> list:
    _seed_default()
    out = []
    for name, cfg in sorted(_load_dests().items()):
        item = {"name": name, "kind": cfg.get("kind")}
        if cfg.get("kind") == "local":
            item["root"] = cfg.get("root")
        else:
            item.update({"host": cfg.get("host"), "port": cfg.get("port", 22),
                         "user": cfg.get("user"), "path": cfg.get("path"),
                         "has_key": bool(cfg.get("key_path"))})
        out.append(item)
    return out


def save_dest(cfg: dict) -> dict:
    name = str(cfg.get("name", "")).strip()
    kind = str(cfg.get("kind", "")).strip()
    if not _NAME_RE.match(name):
        raise DeployError("نام مقصد نامعتبر (حروف کوچک/عدد/-)")
    if kind not in ("local", "remote"):
        raise DeployError("kind باید local یا remote باشد")
    bad = _SECRET_KEY_NAMES.intersection(k.lower() for k in cfg)
    if bad:
        raise DeployError("ذخیرهٔ %s در مقصد مجاز نیست — احراز هویت فقط با SSH Key"
                          % ", ".join(sorted(bad)))
    out = {"name": name, "kind": kind}
    if kind == "local":
        root = str(cfg.get("root", "")).strip() or str(LOCAL_ROOT)
        if not os.path.isabs(root):
            raise DeployError("root مقصد local باید مسیر مطلق باشد")
        out["root"] = root
    else:
        host = str(cfg.get("host", "")).strip()
        user = str(cfg.get("user", "")).strip()
        path = str(cfg.get("path", "")).strip()
        if not host or any(c.isspace() for c in host):
            raise DeployError("host نامعتبر")
        if not user or any(c.isspace() for c in user):
            raise DeployError("user نامعتبر")
        if not path.startswith("/") or any(c.isspace() for c in path):
            raise DeployError("path مقصد remote باید مسیر مطلق بدون فاصله باشد")
        ssh_port = int(cfg.get("port") or 22)
        if not (1 <= ssh_port <= 65535):
            raise DeployError("پورت SSH نامعتبر")
        out.update({"host": host, "port": ssh_port, "user": user, "path": path})
        key_path = str(cfg.get("key_path", "")).strip()
        if key_path:
            if any(c.isspace() for c in key_path) or not os.path.isabs(key_path):
                raise DeployError("key_path باید مسیر مطلق بدون فاصله باشد")
            out["key_path"] = key_path
    dests = _load_dests()
    dests[name] = out
    _save_dests(dests)
    return {"name": name, "kind": kind}


def delete_dest(name: str) -> None:
    dests = _load_dests()
    if name not in dests:
        raise DeployError("مقصد یافت نشد: %s" % name, 404)
    del dests[name]
    _save_dests(dests)


# ── history ──────────────────────────────────────────────────────────────────

def _load_history() -> list:
    if HISTORY_FILE.is_file():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return []


def _save_history(h: list) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(h, ensure_ascii=False, indent=1),
                            encoding="utf-8")


def history(project: str = "", dest: str = "") -> list:
    h = _load_history()
    if project:
        h = [r for r in h if r.get("project") == project]
    if dest:
        h = [r for r in h if r.get("dest") == dest]
    return sorted(h, key=lambda r: r.get("finished") or 0, reverse=True)[:50]


def _successes(project: str, dest: str) -> list:
    return sorted([r for r in _load_history()
                   if r.get("project") == project and r.get("dest") == dest
                   and r.get("status") == "success"
                   and r.get("action") in ("deploy", "rollback")],
                  key=lambda r: r.get("finished") or 0)


# ── lock (no concurrent / destructive deploys) ───────────────────────────────

class _GlobalLock:
    def __init__(self):
        self._fd = None

    def __enter__(self):
        if not _PROC_LOCK.acquire(blocking=False):
            raise DeployError("استقرار دیگری در همین فرایند در جریان است", 409)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - LOCK_FILE.stat().st_mtime
            except OSError:
                age = 0
            if age > BUSY_LOCK_MAX_AGE:
                LOCK_FILE.unlink(missing_ok=True)
                self._fd = os.open(str(LOCK_FILE),
                                   os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            else:
                _PROC_LOCK.release()
                raise DeployError("استقرار دیگری در جریان است — بعداً تلاش کنید", 409)
        return self

    def __exit__(self, *exc):
        if self._fd is not None:
            os.close(self._fd)
            LOCK_FILE.unlink(missing_ok=True)
        _PROC_LOCK.release()
        return False


# ── helpers ──────────────────────────────────────────────────────────────────

def _port_free(p: int) -> bool:
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # TIME_WAIT-safe
    s.settimeout(1)
    try:
        s.bind(("127.0.0.1", p))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _alloc_port() -> int:
    for p in PORT_RANGE:
        if _port_free(p):
            return p
    raise DeployError("پورت آزاد در بازهٔ استقرار یافت نشد", 503)


def latest_release(project: str) -> Path:
    cands = sorted(config.EXPORTS_DIR.glob("%s-release-*.zip" % project))
    if not cands:
        raise DeployError("هیچ بستهٔ انتشاری برای %s وجود ندارد — ابتدا بسته بسازید" % project, 404)
    return cands[-1]


def _check_release(project: str, release_path: str | None) -> tuple:
    rel = Path(release_path) if release_path else latest_release(project)
    if rel.parent.resolve() != config.EXPORTS_DIR.resolve() \
       or not rel.name.startswith("%s-release-" % project) \
       or not rel.name.endswith(".zip") or not rel.is_file():
        raise DeployError("بستهٔ انتشار نامعتبر یا خارج از exports/", 400)
    try:
        v = releasemod.verify_release(rel, project)
        with zipfile.ZipFile(rel) as zf:
            meta = json.loads(zf.read("%s/release.json" % project)
                              .decode("utf-8"))
    except zipfile.BadZipFile:
        raise DeployError("بستهٔ انتشار خراب است (zip ناخوانا)", 400) from None
    if not v.get("ok"):
        reasons = list(v.get("problems") or []) + \
            [k for k, val in (v.get("checks") or {}).items() if not val]
        raise DeployError("بستهٔ انتشار از نظر ساختاری نامعتبر: %s"
                          % "; ".join(reasons), 400)
    return rel, meta


def _extract(rel: Path, target_root: Path) -> Path:
    """Extract release zip (arcname <project>/...) into target_root safely."""
    target_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(rel) as zf:
        for n in zf.namelist():
            dest = (target_root / n).resolve()
            if not str(dest).startswith(str(target_root.resolve())):
                raise DeployError("مسیر ناامن داخل بسته: %s" % n, 400)
        zf.extractall(target_root)
    return target_root


def _http(url: str, timeout: float = 3.0) -> int:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return 0


def _health(url: str, kind: str) -> dict:
    primary = "/api/health" if kind == "python-server" else "/"
    for path in ([primary, "/"] if kind == "python-server" else [primary]):
        for _ in range(24):
            code = _http(url + path)
            if code == 200:
                return {"ok": True, "url": url + path, "detail": "200"}
            time.sleep(0.5)
    return {"ok": False, "url": url + primary,
            "detail": "پاسخ سالم دریافت نشد"}


def _stop_local(record: dict, target: Path) -> dict:
    pid = record.get("pid")
    if not pid:
        return {"ok": True, "detail": "پروسه‌ای ثبت نشده"}
    try:
        cwd = os.readlink("/proc/%d/cwd" % pid)
    except OSError:
        return {"ok": True, "detail": "پروسهٔ قبلی از قبل خاموش است"}
    if Path(cwd).resolve() != target.resolve():
        return {"ok": True, "detail": "pid متعلق به مسیر دیگری است — دست نخورد"}
    try:
        os.kill(pid, 15)
        for _ in range(10):
            if not os.path.exists("/proc/%d" % pid):
                break
            time.sleep(0.3)
        else:
            os.kill(pid, 9)
        return {"ok": True, "detail": "پروسهٔ قبلی متوقف شد (pid=%d)" % pid}
    except OSError as exc:
        return {"ok": False, "detail": "توقف پروسه ناموفق: %s" % exc}


# ── local deployment ─────────────────────────────────────────────────────────

def _deploy_local(project, dest, rel, meta, steps, action) -> dict:
    root = Path(dest["root"])
    root.mkdir(parents=True, exist_ok=True)
    target = root / project
    prevs = _successes(project, dest["name"])
    prev = prevs[-1] if prevs else None

    if prev:
        steps.append({"step": "stop-previous", **_stop_local(prev, target)})

    port = None
    if prev and prev.get("port") and _port_free(prev["port"]):
        port = prev["port"]
        steps.append({"step": "port", "ok": True,
                      "detail": "پورت قبلی محصول دوباره اختصاص یافت: %d" % port})
    if port is None:
        port = _alloc_port()
        steps.append({"step": "port", "ok": True, "detail": "پورت آزاد: %d" % port})

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    dep_id = "dep-%s" % time.strftime("%Y%m%d-%H%M%S")
    log_path = LOGS_DIR / ("%s-%s.log" % (project, dep_id))
    steps.append({"step": "extract", "ok": True,
                  "detail": "استخراج %s → %s" % (rel.name, target)})
    if target.exists():
        shutil.rmtree(target)
    _extract(rel, root)
    if not (target / "run.sh").is_file():
        steps.append({"step": "start", "ok": False,
                      "detail": "run.sh در بسته نیست"})
        raise DeployError("بستهٔ استخراج‌شده run.sh ندارد", 400)

    # AF-29 — a deployed bundle's run.sh must NOT inherit provider secrets.
    from core import boundary
    env = boundary.clean_env({"PORT": str(port),
                               "AI_FACTORY_PORT": str(port)})
    with open(log_path, "ab") as lf:
        proc = subprocess.Popen(["sh", "run.sh"], cwd=str(target), env=env,
                                stdout=lf, stderr=subprocess.STDOUT,
                                start_new_session=True)
    steps.append({"step": "start", "ok": True,
                  "detail": "اجرای مستقل: sh run.sh (PORT=%d، pid=%d، log=%s)"
                            % (port, proc.pid, log_path.name)})

    url = "http://127.0.0.1:%d" % port
    h = _health(url, meta["technology"]["kind"])
    steps.append({"step": "health", "ok": h["ok"],
                  "detail": "%s → %s" % (h["url"], h["detail"])})
    # AF-36 — record the same health evidence through ActionGate so deploy
    # success is traceable and cannot be asserted without a verified probe.
    from core import action_gate
    g = action_gate.gate_deploy({"ok": h["ok"], "http": 200 if h["ok"] else None,
                                 "path": h["url"]})
    steps.append({"step": "deploy-gate", "ok": g["status"] == "PASS",
                  "detail": g["status"]})
    if not h["ok"]:
        try:
            os.kill(proc.pid, 9)
            steps.append({"step": "cleanup", "ok": True,
                          "detail": "پروسهٔ ناسالم متوقف شد (pid=%d)" % proc.pid})
        except OSError:
            pass
        raise DeployError("Health Check ناموفق پس از استقرار: %s" % h["detail"], 502)

    return {"pid": proc.pid, "port": port, "url": url, "log": str(log_path),
            "id": dep_id}


# ── remote deployment (ssh/scp, key auth only) ───────────────────────────────

def _ssh_base(dest: dict) -> list:
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
           "-o", "StrictHostKeyChecking=accept-new", "-p", str(dest["port"])]
    if dest.get("key_path"):
        cmd += ["-i", dest["key_path"]]
    cmd.append("%s@%s" % (dest["user"], dest["host"]))
    return cmd


def _run(cmd: list, timeout: float = 60.0, inp: bytes | None = None) -> tuple:
    p = subprocess.run(cmd, capture_output=True, timeout=timeout, input=inp)
    return p.returncode, p.stdout.decode("utf-8", "replace"), \
        p.stderr.decode("utf-8", "replace")


def _deploy_remote(project, dest, rel, meta, steps, action) -> dict:
    for tool in ("ssh", "scp"):
        if not shutil.which(tool):
            steps.append({"step": "tools", "ok": False,
                          "detail": "%s نصب نیست — مقصد remote نیازمند openssh است" % tool})
            raise DeployError("ابزار %s در دسترس نیست؛ پیش‌نیاز مقصد remote برآورده نشد" % tool,
                              400)
    steps.append({"step": "tools", "ok": True, "detail": "ssh/scp موجود"})
    if dest.get("key_path") and not Path(dest["key_path"]).is_file():
        steps.append({"step": "key", "ok": False,
                      "detail": "کلید SSH یافت نشد: مسیر ثبت‌شده وجود ندارد"})
        raise DeployError("کلید SSH مشخص‌شده وجود ندارد", 400)
    steps.append({"step": "key", "ok": True,
                  "detail": "احراز هویت با SSH Key (%s)"
                            % ("مسیر ثبت‌شده" if dest.get("key_path") else "پیش‌فرض عامل")})

    ssh = _ssh_base(dest)
    rpath = dest["path"].rstrip("/") + "/" + project
    rcode, out, err = _run(ssh + ["true"], timeout=20)
    steps.append({"step": "connect", "ok": rcode == 0,
                  "detail": "اتصال %s@%s:%s → %s"
                            % (dest["user"], dest["host"], dest["port"],
                               "موفق" if rcode == 0 else (err or out).strip()[:120])})
    if rcode != 0:
        raise DeployError("اتصال SSH ناموفق — دسترسی/کلید را بررسی کنید", 502)

    rcode, out, err = _run(ssh + ["command -v scp >/dev/null 2>&1 && echo yes || echo no"],
                           timeout=20)
    rscp = out.strip() == "yes"
    steps.append({"step": "remote-scp", "ok": rscp,
                  "detail": "scp روی مقصد: %s" % ("موجود" if rscp
                                                  else "نصب نیست — پیش‌نیاز انتقال فایل")})
    if not rscp:
        raise DeployError("scp روی مقصد موجود نیست؛ انتقال فایل ممکن نیست", 400)
    rcode, out, err = _run(ssh + ["command -v curl >/dev/null 2>&1 && echo yes || echo no"],
                           timeout=20)
    rcurl = out.strip() == "yes"
    steps.append({"step": "remote-curl", "ok": rcurl,
                  "detail": "curl روی مقصد: %s (برای Health Check)"
                            % ("موجود" if rcurl else "نصب نیست")})
    if not rcurl:
        raise DeployError("curl روی مقصد موجود نیست؛ Health Check ممکن نیست", 400)

    port = None
    prevs = _successes(project, dest["name"])
    if prevs and prevs[-1].get("port"):
        port = prevs[-1]["port"]
    if port is None:
        port = _alloc_port()
    steps.append({"step": "port", "ok": True,
                  "detail": "پورت محصول روی مقصد: %d" % port})

    _run(ssh + ["[ -f %s/.deploy.pid ] && kill $(cat %s/.deploy.pid) 2>/dev/null; true"
                % (rpath, rpath)], timeout=20)
    _run(ssh + ["mkdir -p %s" % dest["path"]], timeout=20)
    _run(ssh + ["rm -rf %s" % rpath], timeout=30)
    stage_root = config.PROJECT_ROOT / "logs" / "deploy"
    stage_root.mkdir(parents=True, exist_ok=True)
    staging = stage_root / ("stage-" + project)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    _extract(rel, staging)
    src = staging / project
    if not src.is_dir():
        raise DeployError("ساختار بستهٔ استخراج‌شده غیرمنتظره است", 400)

    rcode, out, err = _run(["scp", "-O",   # legacy scp protocol — works on
                                                  # minimal sshd without sftp subsystem
                            "-o", "BatchMode=yes", "-o",
                            "StrictHostKeyChecking=accept-new",
                            "-P", str(dest["port"])]
                           + (["-i", dest["key_path"]] if dest.get("key_path") else [])
                           + ["-r", str(src), "%s@%s:%s/"
                              % (dest["user"], dest["host"], dest["path"])],
                           timeout=120)
    shutil.rmtree(staging, ignore_errors=True)
    steps.append({"step": "transfer", "ok": rcode == 0,
                  "detail": "انتقال فایل‌ها → %s" % ("موفق" if rcode == 0
                                                     else (err or out).strip()[:150])})
    if rcode != 0:
        raise DeployError("انتقال SCP ناموفق", 502)

    rcode, out, err = _run(ssh +
                           ["cd %s && { PORT=%d AI_FACTORY_PORT=%d nohup sh run.sh "
                            "</dev/null > .deploy.log 2>&1 & echo $! > .deploy.pid; }"
                            % (rpath, port, port)],
                           timeout=20)
    steps.append({"step": "start", "ok": rcode == 0,
                  "detail": "راه‌اندازی روی مقصد (PORT=%d) → %s"
                            % (port, "موفق" if rcode == 0 else (err or out).strip()[:120])})
    if rcode != 0:
        raise DeployError("راه‌اندازی روی مقصد ناموفق", 502)

    kind = meta["technology"]["kind"]
    hp = "/api/health" if kind == "python-server" else "/"
    hok, hcode = False, ""
    for _ in range(16):   # boot may take a moment on the destination
        rcode, out, err = _run(ssh + ["curl -s -o /dev/null -w '%%{http_code}' "
                                      "http://127.0.0.1:%d%s" % (port, hp)],
                               timeout=30)
        hcode = out.strip()
        if rcode == 0 and hcode == "200":
            hok = True
            break
        time.sleep(0.5)
    steps.append({"step": "health", "ok": hok,
                  "detail": "Health روی مقصد %s%s → %s" % (port, hp,
                                                           hcode or "بدون پاسخ")})
    if not hok:
        raise DeployError("Health Check روی مقصد ناموفق", 502)
    return {"port": port, "url": "http://%s:%d" % (dest["host"], port),
            "log": None, "id": "dep-%s" % time.strftime("%Y%m%d-%H%M%S")}


# ── public operations ────────────────────────────────────────────────────────

def preflight(project: str, dest_name: str,
              release_path: str | None = None) -> dict:
    """Readiness report — NO changes, NO destructive action."""
    steps = []
    ok = True
    try:
        dest = get_dest(dest_name)
        steps.append({"step": "dest", "ok": True,
                      "detail": "مقصد %s (%s)" % (dest["name"], dest["kind"])})
    except DeployError as exc:
        return {"ok": False, "steps": [{"step": "dest", "ok": False,
                                        "detail": str(exc)}]}
    try:
        rel, meta = _check_release(project, release_path)
        steps.append({"step": "release", "ok": True,
                      "detail": "%s (version=%s, build=%s, kind=%s, deps=%d)"
                                % (rel.name, meta.get("version"), meta.get("build"),
                                   meta["technology"]["kind"],
                                   len(meta["technology"].get("deps") or []))})
        steps.append({"step": "entry", "ok": True,
                      "detail": "entry=%s؛ برای استقرار از run.sh تولیدی استفاده می‌شود"
                                % meta["technology"].get("entry")})
    except DeployError as exc:
        steps.append({"step": "release", "ok": False, "detail": str(exc)})
        return {"ok": False, "steps": steps}

    if dest["kind"] == "local":
        try:
            root = Path(dest["root"])
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".r4-write-probe"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
            steps.append({"step": "destination", "ok": True,
                          "detail": "مسیر مقصد قابل نوشتن: %s" % root})
        except OSError as exc:
            steps.append({"step": "destination", "ok": False,
                          "detail": "مسیر مقصد قابل نوشتن نیست: %s" % exc})
            return {"ok": False, "steps": steps}
        port = _alloc_port()
        steps.append({"step": "port", "ok": True,
                      "detail": "پورت آزاد در بازهٔ استقرار: %d "
                                "(بدون تداخل با 8580/4556/8610-8659)" % port})
    else:
        for tool in ("ssh", "scp"):
            have = bool(shutil.which(tool))
            steps.append({"step": "tools", "ok": have,
                          "detail": "%s: %s" % (tool, "موجود" if have else "نصب نیست")})
            ok = ok and have
        if dest.get("key_path"):
            have = Path(dest["key_path"]).is_file()
            steps.append({"step": "key", "ok": have,
                          "detail": "کلید ثبت‌شده: %s" % ("موجود" if have else "یافت نشد")})
            ok = ok and have
        else:
            steps.append({"step": "key", "ok": True,
                          "detail": "کلید ثبت‌شده ندارد (احراز هویت پیش‌فرض عامل/کلید)"})
        rcode, out, err = _run(_ssh_base(dest) + ["true"], timeout=20)
        steps.append({"step": "connect", "ok": rcode == 0,
                      "detail": "اتصال %s@%s:%s → %s" % (dest["user"], dest["host"],
                                                         dest["port"],
                                                         "موفق" if rcode == 0
                                                         else (err or out).strip()[:120])})
        if rcode != 0:
            return {"ok": False, "steps": steps}
        rcode, out, err = _run(_ssh_base(dest) +
                               ["command -v scp >/dev/null 2>&1 && echo yes || echo no"],
                               timeout=20)
        rscp = out.strip() == "yes"
        steps.append({"step": "remote-scp", "ok": rscp,
                      "detail": "scp روی مقصد: %s" % ("موجود" if rscp
                                                      else "نصب نیست — برای انتقال لازم است")})
        if not rscp:
            return {"ok": False, "steps": steps}
        rcode, out, err = _run(_ssh_base(dest) +
                               ["command -v curl >/dev/null 2>&1 && echo yes || echo no"],
                               timeout=20)
        rcurl = out.strip() == "yes"
        steps.append({"step": "remote-curl", "ok": rcurl,
                      "detail": "curl روی مقصد: %s (برای Health Check)"
                                % ("موجود" if rcurl else "نصب نیست")})
        if not rcurl:
            return {"ok": False, "steps": steps}
    steps.append({"step": "confirm-policy", "ok": True,
                  "detail": "استقرار/rollback نیازمند تأیید صریح کاربر است (confirm=true)"})
    return {"ok": all(s["ok"] for s in steps), "steps": steps}


def deploy(project: str, dest_name: str, release_path: str | None = None,
           confirm: bool = False) -> dict:
    if not confirm:
        raise DeployError("استقرار بدون تأیید صریح مجاز نیست (confirm=true)", 400)
    if not paths.project_dir(project).is_dir():
        raise DeployError("پروژه یافت نشد: %s" % project, 404)
    dest = get_dest(dest_name)
    rel, meta = _check_release(project, release_path)
    started = time.time()
    steps = [{"step": "release", "ok": True,
              "detail": "%s (version=%s, build=%s, kind=%s)"
                        % (rel.name, meta.get("version"), meta.get("build"),
                           meta["technology"]["kind"])}]
    rec = {"id": "dep-%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), int(time.time() % 1000)),
           "project": project, "dest": dest["name"], "dest_kind": dest["kind"],
           "release_path": rel.name, "version": meta.get("version"),
           "build": meta.get("build"), "tech_kind": meta["technology"]["kind"],
           "entry": meta["technology"].get("entry"),
           "action": "deploy", "status": "failed", "steps": steps,
           "started": started, "finished": None, "error": None,
           "url": None, "port": None, "pid": None}
    # NOTE: قفل بیرون از try است — خطای «استقرار هم‌زمان» به‌صورت 409 تمیز
    # به API می‌رسد و رکورد failed بی‌مصرف در تاریخچه نمی‌سازد.
    with _GlobalLock():
        try:
            if dest["kind"] == "local":
                res = _deploy_local(project, dest, rel, meta, rec["steps"], "deploy")
            else:
                res = _deploy_remote(project, dest, rel, meta, rec["steps"], "deploy")
            rec.update({"status": "success", "url": res["url"], "port": res["port"],
                        "pid": res.get("pid"), "log": res.get("log"), "id": res["id"]})
        except DeployError as exc:
            rec["error"] = str(exc)
            rec["steps"].append({"step": "error", "ok": False, "detail": str(exc)})
        except Exception as exc:  # unexpected — recorded honestly
            rec["error"] = "خطای غیرمنتظره: %r" % exc
            rec["steps"].append({"step": "error", "ok": False, "detail": repr(exc)})
            log.exception("deploy failed: %s", project)
        rec["finished"] = time.time()
        h = _load_history()
        h.append(rec)
        _save_history(h)
        log.info("deploy %s → %s/%s: %s", project, dest["name"], rel.name, rec["status"])
    return rec


def rollback(project: str, dest_name: str, confirm: bool = False) -> dict:
    if not confirm:
        raise DeployError("Rollback بدون تأیید صریح مجاز نیست (confirm=true)", 400)
    succ = _successes(project, dest_name)
    if len(succ) < 1:
        raise DeployError("هیچ استقرار موفقی برای بازگشت وجود ندارد", 404)
    current = succ[-1]
    previous = None
    for r in reversed(succ[:-1]):
        if r.get("release_path") != current.get("release_path") \
           or r.get("build") != current.get("build"):
            previous = r
            break
    if previous is None:
        raise DeployError("نسخهٔ قبلی متفاوتی برای بازگشت وجود ندارد", 404)
    dest = get_dest(dest_name)
    rel = config.EXPORTS_DIR / previous["release_path"]
    if not rel.is_file():
        raise DeployError("بستهٔ نسخهٔ قبلی در exports موجود نیست", 404)
    # P10-1 — همان گودال deploy: راستی‌آزمایی کامل بستهٔ هدفِ بازگشت؛
    # zip خراب/ناقص = DeployError تمیز، هرگز بازکردن خام zip.
    rel, meta = _check_release(project, str(rel))
    started = time.time()
    steps = [{"step": "target", "ok": True,
              "detail": "بازگشت به %s (build=%s) از %s"
                        % (previous["release_path"], previous["build"], current["id"])}]
    rec = {"id": "dep-%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), int(time.time() % 1000)),
           "project": project, "dest": dest["name"], "dest_kind": dest["kind"],
           "release_path": previous["release_path"], "version": previous.get("version"),
           "build": previous.get("build"), "tech_kind": current.get("tech_kind"),
           "entry": current.get("entry"), "action": "rollback", "status": "failed",
           "steps": steps, "started": started, "finished": None, "error": None,
           "url": None, "port": None, "pid": None,
           "rolled_back_from": current["id"]}
    with _GlobalLock():
        try:
            if dest["kind"] == "local":
                res = _deploy_local(project, dest, rel, meta, rec["steps"], "rollback")
            else:
                res = _deploy_remote(project, dest, rel, meta, rec["steps"], "rollback")
            rec.update({"status": "success", "url": res["url"], "port": res["port"],
                        "pid": res.get("pid"), "log": res.get("log"), "id": res["id"]})
        except DeployError as exc:
            rec["error"] = str(exc)
            rec["steps"].append({"step": "error", "ok": False, "detail": str(exc)})
        rec["finished"] = time.time()
    h = _load_history()
    h.append(rec)
    _save_history(h)
    return rec


def stop(project: str, dest_name: str) -> dict:
    dest = get_dest(dest_name)
    succ = _successes(project, dest_name)
    if not succ:
        raise DeployError("استقرار فعالی برای توقف وجود ندارد", 404)
    current = succ[-1]
    rec = {"id": "dep-%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), int(time.time() % 1000)),
           "project": project, "dest": dest["name"], "dest_kind": dest["kind"],
           "release_path": current.get("release_path"), "action": "stop",
           "status": "failed", "steps": [], "started": time.time(),
           "finished": None, "error": None, "url": current.get("url"),
           "port": current.get("port"), "pid": current.get("pid")}
    if dest["kind"] != "local":
        raise DeployError("توقف remote فعلاً از طریق همان مقصد انجام می‌شود", 400)
    target = Path(dest["root"]) / project
    res = _stop_local(current, target)
    rec["status"] = "success" if res["ok"] else "failed"
    rec["steps"] = [{"step": "stop", **res}]
    rec["finished"] = time.time()
    h = _load_history()
    h.append(rec)
    _save_history(h)
    return rec
