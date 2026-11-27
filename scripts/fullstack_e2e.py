"""AI Factory v0.4.0 — fullstack acceptance suite (18 mandatory scenarios).

Real E2E: talks to the panel API (create/scaffold/run/stop/export) and to the
scaffolded app over real HTTP with a cookie-aware client (register/login/
CSRF/CRUD/persistence). Nothing is faked; every check is a live request.
Cleanup removes the two temporary projects it created.
"""
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(os.environ["ROOT"])
BASE = os.environ["BASE"]
TS = time.strftime("%H%M%S")
PJA = f"af4fs-a-{TS}"     # project A (created + scaffolded by /create)
PJB = f"af4fs-b-{TS}"     # project B (created, scaffolded via /scaffold)

PASS = 0
FAIL = 0
FAILED = []

def _panel_pass() -> str:
    """AF-05.1: credential from env or the secure local file — never a
    hardcoded default; fails safely (non-leaking) when neither exists."""
    pw = os.environ.get("PANEL_PASS") or ""
    if not pw:
        try:
            pw = (Path.home() / ".af-panel" / "panel-pass.txt").read_text(
                encoding="utf-8").strip()
        except OSError:
            pw = ""
    if not pw:
        sys.exit("panel credential missing — set PANEL_PASS or create "
                 "~/.af-panel/panel-pass.txt (0600)")
    return pw


PANEL_PASS = _panel_pass()
_panel_session, _panel_csrf = "", ""


def _panel_login():
    """P6-2: first-run init (idempotent) + login; store session/CSRF."""
    global _panel_session, _panel_csrf
    try:
        st = json.loads(urllib.request.urlopen(
            BASE + "/api/panel/state", timeout=15).read().decode())
    except Exception:
        st = {}
    if not st.get("initialized"):
        self_ = urllib.request.Request(
            BASE + "/api/panel/init", method="POST",
            data=json.dumps({"password": PANEL_PASS}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(self_, timeout=15).read()
    req = urllib.request.Request(
        BASE + "/api/panel/login", method="POST",
        data=json.dumps({"password": PANEL_PASS}).encode(),
        headers={"Content-Type": "application/json"})
    r = urllib.request.urlopen(req, timeout=15)
    for sc in (r.headers.get_all("Set-Cookie") or []):
        for c in sc.split(", "):
            part = c.split(";")[0]
            if part.startswith("panel_session="):
                _panel_session = part.split("=", 1)[1]
            elif part.startswith("panel_csrf="):
                _panel_csrf = part.split("=", 1)[1]


def api(method, path, body=None):
    """Call the AI Factory panel API; return (status, dict)."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    cookies = []
    if _panel_session:
        cookies.append("panel_session=" + _panel_session)
    if _panel_csrf:
        cookies.append("panel_csrf=" + _panel_csrf)     # double-submit
        headers["X-Panel-CSRF"] = _panel_csrf
    if cookies:
        headers["Cookie"] = "; ".join(cookies)
    req = urllib.request.Request(
        BASE + path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            code = r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        code = e.code
    try:
        return code, json.loads(raw)
    except ValueError:
        return code, {}


class AppClient:
    """Cookie-aware HTTP client for the scaffolded app under test."""

    def __init__(self, base):
        self.base = base
        self.jar = {}          # cookie name -> value

    def _cookie_header(self):
        return "; ".join(f"{k}={v}" for k, v in self.jar.items())

    def _absorb(self, set_cookies):
        for c in set_cookies or []:
            pair = c.split(";")[0]
            name, _, val = pair.partition("=")
            name = name.strip()
            if name and val:
                self.jar[name] = val
            elif name == "af_session":       # cleared cookie
                self.jar.pop(name, None)

    def csrf(self):
        code, d, _ = self.call("GET", "/api/csrf")
        if code != 200:
            raise RuntimeError("csrf endpoint failed: %s" % code)
        return d["csrf_token"]

    def call(self, method, path, body=None, csrf=None, headers=None):
        h = {}
        if body is not None:
            h["Content-Type"] = "application/json"
        if csrf:
            h["X-CSRF-Token"] = csrf
        elif "X-CSRF-Token" not in h and self.jar.get("af_csrf"):
            h["X-CSRF-Token"] = self.jar["af_csrf"]   # double-submit echo
        h.update(headers or {})
        ch = self._cookie_header()
        if ch:
            h["Cookie"] = ch
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, method=method,
                                     data=data, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read()
                code = r.status
                setc = r.headers.get_all("Set-Cookie") or []
        except urllib.error.HTTPError as e:
            raw = e.read()
            code = e.code
            setc = e.headers.get_all("Set-Cookie") or []
        self._absorb(setc)
        try:
            return code, json.loads(raw.decode()), setc
        except (ValueError, UnicodeDecodeError):
            return code, raw, setc

    # shorthand
    def get(self, path, headers=None):
        return self.call("GET", path, None, None, headers)

    def post(self, path, body=None, csrf=None, headers=None):
        return self.call("POST", path, body, csrf, headers)

    def delete(self, path, body=None, csrf=None, headers=None):
        return self.call("DELETE", path, body, csrf, headers)


def wait_port(port, tries=50):
    for _ in range(tries):
        try:
            s = socket.create_connection(("127.0.0.1", port), 1)
            s.close()
            return True
        except OSError:
            time.sleep(0.4)
    return False


def port_open(port):
    try:
        s = socket.create_connection(("127.0.0.1", port), 1)
        s.close()
        return True
    except OSError:
        return False


def check(name, cond, info=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✅ %s" % name)
    else:
        FAIL += 1
        FAILED.append(name)
        print("  ❌ %s%s%s" % (name, " — " if info else "", info))


def run_project(proj):
    code, r = api("POST", "/api/factory/run", {"project": proj})
    assert code == 200 and r.get("ok"), "run failed: %s %s" % (code, r)
    port = r["port"]
    assert wait_port(port), "port %d never opened" % port
    return port


def stop_project(proj):
    return api("POST", "/api/factory/stop", {"project": proj})


def cleanup():
    try:
        for p in (PJA, PJB):
            stop_project(p)
            api("POST", "/api/factory/delete", {"project": p, "confirm": True})
    except Exception as exc:  # noqa: BLE001
        print("cleanup warning: %s" % exc)


def main():
    print("== آزمون پذیرش Full-Stack (AI Factory v0.4.0) ==")
    _panel_login()

    # ── 1) create fullstack project ────────────────────────────────────────
    code, r = api("POST", "/api/factory/projects", {
        "name": PJA, "type": "fullstack-python", "description": "e2e v0.4.0"})
    check("۱) ایجاد پروژه Full-Stack",
          code == 201 and r.get("project", {}).get("type") == "fullstack-python",
          "code=%s" % code)

    # ── 2) essential files exist ───────────────────────────────────────────
    d = ROOT / "projects" / PJA
    need = ["server.py", "app/api.py", "app/auth.py", "app/db.py",
            "ui/index.html", "ui/app.js", "ui/style.css", "ui/user.html",
            "ui/admin.html", "tests/test_api.py", "README.md",
            "config.json", "requirements.txt"]
    missing = [f for f in need if not (d / f).is_file()]
    check("۲) فایل‌های ضروری قالب", not missing, "missing=%s" % missing)
    check("۲ب) جای‌گذاری عنوان انجام شد",
          "{{APP_TITLE}}" not in (d / "server.py").read_text())

    # ── 3) run backend ─────────────────────────────────────────────────────
    port = run_project(PJA)
    cl = AppClient("http://127.0.0.1:%d" % port)
    code, d2, _ = cl.get("/api/csrf")
    ok = code == 200 and isinstance(d2, dict) and d2.get("csrf_token")
    check("۳) اجرای بک‌اند", ok, "code=%s" % code)

    # ── 4) database created ────────────────────────────────────────────────
    dbf = d / "data" / "app.db"
    deadline = time.time() + 10
    while not dbf.exists() and time.time() < deadline:
        time.sleep(0.3)
    check("۴) دیتابیس SQLite ساخته شد", dbf.exists())

    # ── 5) register user (first becomes admin) ─────────────────────────────
    code, r, _ = cl.post("/api/auth/register", {
        "username": "boss", "password": "secret123"})
    check("۵) ثبت‌نام کاربر (اولین=مدیر)",
          code == 201 and r.get("user", {}).get("role") == "admin",
          "code=%s r=%s" % (code, r))

    code, r, _ = cl.get("/api/me")
    check("۵ب) نشست پس از ثبت‌نام فعال است",
          code == 200 and r.get("user", {}).get("username") == "boss")

    # ── 6) login OK ─────────────────────────────────────────────────────────
    code, r, _ = cl.post("/api/auth/login", {
        "username": "boss", "password": "secret123"})
    check("۶) ورود موفق", code == 200 and r.get("ok"), "code=%s" % code)

    # ── 7) login wrong password ────────────────────────────────────────────
    code, r, _ = cl.post("/api/auth/login", {
        "username": "boss", "password": "WRONG"})
    check("۷) ورود با رمز اشتباه ۴۰۱", code == 401, "code=%s" % code)

    # ── 8) logout ───────────────────────────────────────────────────────────
    code, r, _ = cl.post("/api/auth/logout", {})
    ok_logout = code == 200
    code, r, _ = cl.get("/api/me")
    check("۸) خروج از حساب و ۴۰۱ بعدش",
          ok_logout and code == 401, "logout=%s me=%s" % (ok_logout, code))
    # login again for later steps
    code, r, _ = cl.post("/api/auth/login", {
        "username": "boss", "password": "secret123"})
    check("۸ب) ورود مجدد برای مراحل بعد", code == 200)

    # ── 9) admin protection: plain user blocked from admin panel ──────────
    # separate client => separate session
    cl2 = AppClient(cl.base)
    cl2.csrf()
    code, r, _ = cl2.post("/api/auth/register", {
        "username": "worker", "password": "goodpass1"})
    w_role = r.get("user", {}).get("role")
    code_u, _, _ = cl2.get("/api/admin/users")
    # anonymous client
    cl_anon = AppClient(cl.base)
    code_a, _, _ = cl_anon.get("/api/admin/users")
    code_ok, r, _ = cl.get("/api/admin/users")
    check("۹) کاربر عادی از پنل مدیر بلاک شد",
          w_role == "user" and code_u == 403 and code_a == 401 and code_ok == 200,
          "role=%s user=%s anon=%s admin=%s" % (w_role, code_u, code_a, code_ok))

    # ── 10) real-data CRUD (persian names hit the real DB) ────────────────
    code, r, _ = cl.post("/api/customers", {
        "name": "شرکت داده‌گستر", "phone": "021-4455", "note": "پروژه X"})
    cid = r.get("customer", {}).get("id")
    check("۱۰) ایجاد مشتری واقعی", code == 201 and cid is not None,
          "code=%s r=%s" % (code, r))

    code, r, _ = cl.post("/api/customers/%d/update" % cid, {
        "name": "شرکت داده‌گستر نوین", "phone": "021-9988"})
    ok_upd = code == 200 and r.get("customer", {}).get("name") == "شرکت داده‌گستر نوین"

    code, r, _ = cl.get("/api/customers")
    listed = any(c["id"] == cid and c["phone"] == "021-9988"
                 for c in r.get("customers", []))

    code, r, _ = cl.delete("/api/customers/%d" % cid, {})
    deleted = code == 200
    code, r, _ = cl.get("/api/customers")
    gone = not any(c["id"] == cid for c in r.get("customers", []))
    check("۱۰ب) به‌روزرسانی/لیست/حذف", ok_upd and listed and deleted and gone,
          "upd=%s listed=%s del=%s gone=%s" % (ok_upd, listed, deleted, gone))

    # worker sees only his own rows
    code, r, _ = cl2.post("/api/customers", {"name": "کافه داخل کوچه"})
    wid = r.get("customer", {}).get("id")
    code, r, _ = cl2.get("/api/customers")
    only_own = ([c["name"] for c in r.get("customers", [])] == ["کافه داخل کوچه"])
    check("۱۰پ) کاربر فقط رکورد خودش را می‌بیند", wid and only_own,
          "wid=%s rows=%s" % (wid, r if isinstance(r, dict) else ""))

    # keep one record for the persistence test
    code, r, _ = cl.post("/api/customers", {
        "name": "رکوردِ دوام", "phone": "021-911"})
    pd_id = r.get("customer", {}).get("id")

    # ── 11) persistence across stop & restart ──────────────────────────────
    stop_project(PJA)
    time.sleep(1.0)
    port2 = run_project(PJA)
    cl3 = AppClient("http://127.0.0.1:%d" % port2)
    cl3.csrf()
    code, r, _ = cl3.post("/api/auth/login", {
        "username": "boss", "password": "secret123"})
    check("۱۱) ورود پس از ری‌استارت", code == 200,
          "code=%s new_port=%d" % (code, port2))
    code, r, _ = cl3.get("/api/customers")
    survived = any(c["id"] == pd_id and c["name"] == "رکوردِ دوام"
                   for c in r.get("customers", []))
    # worker + his record must survive too
    cler = AppClient(cl3.base)
    cler.csrf()
    code, r, _ = cler.post("/api/auth/login", {
        "username": "worker", "password": "goodpass1"})
    code_w, r_w, _ = cler.get("/api/customers")
    wid_survived = any(c["id"] == wid for c in r_w.get("customers", []))
    check("۱۱ب) داده و کاربر پس از ری‌استارت", survived and code == 200 and wid_survived,
          "survived=%s worker=%s wid=%s" % (survived, code, wid_survived))
    cl3.delete("/api/customers/%d" % pd_id, {})

    # ── 12) API validation ──────────────────────────────────────────────────
    code, _, _ = cl3.get("/api/definitely/not/here")
    c404 = code == 404
    # invalid JSON body → 400 at HTTP layer
    req = urllib.request.Request(
        cl3.base + "/api/customers", method="POST", data=b"not-json{{",
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
        c400 = False
    except urllib.error.HTTPError as e:
        c400 = e.code == 400
    check("۱۲) اعتبارسنجی API (۴۰۴ و ۴۰۰)", c404 and c400,
          "404=%s 400=%s" % (c404, c400))

    # ── 13) CSRF protection (must block an AUTHENTICATED session too) ──────
    clx = AppClient(cl3.base)
    clx.csrf()
    clx.post("/api/auth/login", {"username": "boss", "password": "secret123"})
    code, r, _ = cl3.get("/api/me")
    authed = code == 200
    code, r, _ = clx.post("/api/customers", {"name": "بدون هدر csrf"},
                          headers={"X-CSRF-Token": ""})
    b1 = code == 403
    code, r, _ = clx.call("POST", "/api/customers",
                          {"name": "توکن اشتباه", "csrf_token": "0" * 64})
    b2k = code == 403
    nojar = AppClient(cl3.base)
    code, r, _ = nojar.post("/api/auth/login", {
        "username": "boss", "password": "secret123"})   # no cookie + no header
    b3 = code == 403
    check("۱۳) محافظت CSRF (نشست معتبر هم)", authed and b1 and b2k and b3,
          "authed=%s b=%s b2=%s b3=%s" % (authed, b1, b2k, b3))

    # ── 14) input error checks ──────────────────────────────────────────────
    code, r, _ = cl3.post("/api/auth/register", {
        "username": "x", "password": "longenough1"})
    e1 = code == 400
    code, r, _ = cl3.post("/api/auth/register", {
        "username": "somebody", "password": "123"})
    e2 = code == 400
    code, r, _ = cl3.post("/api/customers", {
        "name": "نام شرکت", "phone": "بدون-شکل-درست!!"})
    e3 = code == 400
    check("۱۴) خطاهای ورودی (نام کاربری/رمز/تلفن)", e1 and e2 and e3,
          "e1=%s e2=%s e3=%s" % (e1, e2, e3))

    # ── 15) ZIP health ──────────────────────────────────────────────────────
    code, r = api("POST", "/api/factory/export", {"project": PJA})
    zpath = r.get("path")
    okzip = code == 200 and zpath and Path(zpath).is_file()
    bad = zipfile.ZipFile(zpath).testzip() if okzip else "no-zip"
    check("۱۵) سلامت خروجی ZIP", okzip and bad is None,
          "code=%s bad=%s" % (code, bad))

    # ── 16) secrets excluded from ZIP ───────────────────────────────────────
    names = zipfile.ZipFile(zpath).namelist()
    leaked = [n for n in names
              if n.endswith((".log", ".pyc", ".db", ".db-wal", ".db-shm"))
              or Path(n).name in (".env", "app.db", "app.db-wal", "app.db-shm")
              or ".factory" in Path(n).parts or "__pycache__" in Path(n).parts]
    check("۱۶) فایل‌های محرمانه/رن‌تایم در ZIP نیست", not leaked,
          "leaked=%s" % leaked[:5])

    # ── 17) two concurrent independent projects ─────────────────────────────
    code, r = api("POST", "/api/factory/projects", {
        "name": PJB, "type": "fullstack-python", "description": "e2e-B"})
    code_s, r_s = api("POST", "/api/factory/scaffold", {"project": PJB})
    okB = code == 201 and code_s == 200 and r_s.get("files")
    portB = run_project(PJB)
    check("۱۷) پروژه دوم مستقل اجرا شد",
          okB and port_open(portB) and portB != port2,
          "portB=%d portA=%d" % (portB, port2))
    clB = AppClient("http://127.0.0.1:%d" % portB)
    clB.csrf()
    code, r, _ = clB.post("/api/auth/register", {
        "username": "userB", "password": "pwB123456"})
    regB = code == 201
    # userB does NOT exist in project A
    code, _, _ = cl3.post("/api/auth/login", {
        "username": "userB", "password": "pwB123456"})
    not_in_A = code == 401
    code, _, _ = clB.post("/api/auth/login", {
        "username": "userB", "password": "pwB123456"})
    in_B = code == 200
    check("۱۷ب) جداسازی کاربران دو پروژه", regB and not_in_A and in_B,
          "regB=%s notInA=%s inB=%s" % (regB, not_in_A, in_B))

    # ── 18) correct process stop ────────────────────────────────────────────
    stop_project(PJA)
    stop_project(PJB)
    time.sleep(1.5)
    closedA = not port_open(port2)
    closedB = not port_open(portB)
    code, r = api("GET", "/api/factory/runs")
    none_running = all(not x.get("running")
                       for x in r.get("runs", [])
                       if x["name"] in (PJA, PJB))
    check("۱۸) توقف صحیح پردازش", closedA and closedB and none_running,
          "closedA=%s closedB=%s runs=%s" % (closedA, closedB, none_running))


if __name__ == "__main__":
    try:
        main()
    finally:
        cleanup()
    print("\n== نتیجه: %d موفق، %d ناموفق ==" % (PASS, FAIL))
    if FAILED:
        print("شکست‌ها: " + " | ".join(FAILED))
        sys.exit(1)
    sys.exit(0)
