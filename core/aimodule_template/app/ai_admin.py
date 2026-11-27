"""ai_admin — مدیریت تنظیمات و امنیت هوش مصنوعی در نرم‌افزار تولیدشده.

لایه سیاست روی app/aiclient.py (منطق اتصال فقط در aiclient می‌ماند؛ این فایل
فقط تنظیمات/مدیریت/امنیت را اضافه می‌کند — استاندارد خود و بدون وابستگی بیرونی):

* کلید API فقط در فایل خصوصی data/ai_key.txt (مجوز 0600، نوشتن اتمی؛ فرمت:
  فقط متنِ کلید — همان چیزی که aiclient از api_key_file می‌خواند) — مقدار
  کلید هرگز در config / پاسخ GET / HTML / JS / لاگ / ZIP نمی‌آید؛ فقط مسیر آن
  به‌عنوان ai.<provider>.api_key_file در config.json ثبت می‌شود.
* مدیر: PBKDF2-HMAC-SHA256 با 100,000 تکرار (نمک تصادفی) در data/ai_admin.json
  (0600). فقط salt/hash ذخیره می‌شود. تغییرات فقط با سرصفحه‌ی
  X-AI-Admin=<رمز مدیر>. POST admin/init تنها وقتی passcode ست‌نشده کار می‌کند
  (الگوی first-admin)؛ پیش‌فرض بدون passcode «رد» است (fail closed).
* CSRF (double-submit، هم‌سو با قالب fullstack فابریک): GET admin/csrf توکن را
  با کوکی خواندنی ai_csrf (SameSite=Strict، Path=/) می‌دهد؛ هر mutation باید
  سرصفحه X-AI-CSRF با همان توکن داشته باشد. اعتبار توکن: 24 ساعت.
* ذخیره ادغامی: admin/save فقط فیلدهای غیرمحرمانه‌ی مجاز را عوض می‌کند و
  بقیه‌ی فیلدهای config.json (و بقیه بخش ai) دست‌نخورده می‌مانند؛ فیلدهای
  ناشناخته (هر نامی که شبیه کلید/توکن باشد) با پیام صریح رد می‌شود.

اصلاح سرصفحه‌ها در نرم‌افزار تولیدی: سرصفحه‌ی تغییرات از سرصفحه‌های
X-AI-Admin (رمز مدیر) و X-AI-CSRF (توکن) و کوکی ai_csrf خوانده می‌شود.

اتصال به server.py نرم‌افزار (همه‌ی glue لازم — ۳ خط داخل do_GET/do_POST):

    from app import ai_admin
    if self.path.startswith("/api/ai/") or self.path in ai_admin.UI_FILES:
        return ai_admin.handle(self)

صفحه‌ی تنظیمات:  http://127.0.0.1:<port>/ai.html  (فارسی، RTL، واکنش‌گرا).
"""

import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime
from hashlib import pbkdf2_hmac
from pathlib import Path

try:                              # بستهٔ بست: package یا فایل خالص
    from . import aiclient
except ImportError:               # pragma: no cover
    import aiclient

__all__ = ["route", "handle", "UI_FILES", "csrf_token", "verify_admin"]

_ADMIN_PASS_ITERATIONS = 100_000
_ADMIN_FILE = "ai_admin.json"     # در data/ — همیشه 0600 (JSON: salt/hash)
_KEY_FILE = "ai_key.txt"          # در data/ — همیشه 0600 (فقط متنِ کلیدِ خام)
_KEY_FILE_REL = "data/" + _KEY_FILE
_CSRF_COOKIE = "ai_csrf"
_CSRF_TTL = 24 * 3600
_PASS_MIN = 6

# ----------------------------------------------------------------- static UI -
_UI_DIR = Path(__file__).resolve().parent.parent / "ui"
UI_FILES = {
    "/ai.html": ("text/html; charset=utf-8", _UI_DIR / "ai.html"),
    "/ai.js":   ("application/javascript; charset=utf-8", _UI_DIR / "ai.js"),
    "/ai.css":  ("text/css; charset=utf-8", _UI_DIR / "ai.css"),
}

# ------------------------------------------------------------------ helpers --
_LOCK = threading.RLock()
_CSRF: dict[str, float] = {}      # token → unix expiry (in-memory، ۲۴ ساعت)


def _root() -> Path:
    return aiclient._cfg_root()


def _data_dir() -> Path:
    return _root() / "data"


def _read_private(name: str) -> dict:
    p = _data_dir() / name
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_private(name: str, data: dict) -> None:
    """نوشتن اتمی با مجوز 0600؛ دایرکتوری data هم 0700 می‌شود (best-effort)."""
    d = _data_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    tmp = d / (name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, d / name)


def _read_config() -> tuple[Path, dict | None]:
    """config.json پروژه؛ None یعنی «بود ولی غیرقابل خواندن» (پاک‌شدنی نیست)."""
    f = _root() / "config.json"
    if not f.is_file():
        return f, {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return f, (data if isinstance(data, dict) else {})
    except (OSError, ValueError):
        return f, None


def _write_config(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _cookie_value(cookies: str, name: str) -> str:
    for part in (cookies or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() == name:
                return v.strip()
    return ""

# ------------------------------------------------------------------- admin ---
def _pass_hash(passcode: str, salt: bytes, iterations: int) -> bytes:
    return pbkdf2_hmac("sha256", passcode.encode("utf-8"), salt, iterations)


def admin_initialized() -> bool:
    return bool(_read_private(_ADMIN_FILE).get("hash"))


def verify_admin(headers: dict) -> bool:
    """بررسی رمز مدیر از سرصفحه X-AI-Admin؛ بدون فایل/بدون رمز → رد قطعی."""
    admin = (headers or {}).get("X-AI-Admin", "") or ""
    if not admin or not admin_initialized():
        return False
    st = _read_private(_ADMIN_FILE)
    salt = bytes.fromhex(st.get("salt", ""))
    if not salt:
        return False
    iters = int(st.get("iterations") or _ADMIN_PASS_ITERATIONS)
    if not 1 <= iters <= _ADMIN_PASS_ITERATIONS * 10:
        return False
    calc = _pass_hash(admin, salt, iters).hex()
    ok = hmac.compare_digest(calc, str(st.get("hash", "")).lower())
    if not ok:
        time.sleep(0.05)          # سرعت جستجوی فرسایشی را می‌گیرد (PBKDF2 هم)
    return ok


def csrf_token() -> str:
    """توکن تازه صادر می‌کند (double-submit: کوکی + هدر یکسان)."""
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _LOCK:
        for t, exp in list(_CSRF.items()):
            if exp < now:
                _CSRF.pop(t, None)
        _CSRF[token] = now + _CSRF_TTL
    return token


def _csrf_ok(token: str) -> bool:
    with _LOCK:
        exp = _CSRF.get(token)
        if not exp or exp < time.time():
            _CSRF.pop(token, None)
            return False
        return True


def _check_csrf(body_cookies: str, headers: dict) -> bool:
    cookie = _cookie_value(body_cookies, _CSRF_COOKIE)
    header = (headers or {}).get("X-AI-CSRF", "") or ""
    return bool(header and cookie and
                hmac.compare_digest(header, cookie) and _csrf_ok(header))

# -------------------------------------------------------------- validation ---
def _bad(msg, code=400):
    return code, {"ok": False, "error": msg}, []


def validate_settings(body: dict) -> dict:
    """فیلدهای مجاز را می‌گیرد و بقیه را «با نام» رد می‌کند (بدون ارزش)."""
    body = body or {}
    unknown = sorted(set(body) - {"enabled", "provider", "base_url",
                                  "model", "key_env"})
    if unknown:
        raise aiclient.AiError("no_settings",
                               "فیلدهای مجاز نیستند: " + ", ".join(unknown))
    clean: dict = {}
    if "enabled" in body:
        if body["enabled"] not in (True, False):
            raise aiclient.AiError("no_settings", "«enabled» باید true/false باشد")
        clean["enabled"] = bool(body["enabled"])
    if "provider" in body:
        p = str(body["provider"] or "").strip().lower()
        if not (1 <= len(p) <= 32) or not p.replace("_", "").replace("-", "")\
                                              .isalnum():
            raise aiclient.AiError("no_settings", "نام ارائه‌دهنده نامعتبر است")
        if p not in aiclient.providers():
            raise aiclient.AiError("no_settings",
                                   "ارائه‌دهنده «%s» ثبت نشده است" % p)
        clean["provider"] = p
    if "base_url" in body:
        b = str(body["base_url"] or "").strip().rstrip("/")
        if len(b) > 300 or (b and not b.startswith(("http://", "https://"))):
            raise aiclient.AiError("no_settings",
                                   "آدرس سرویس باید با http(s):// شروع شود")
        clean["base_url"] = b
    if "model" in body:
        m = str(body["model"] or "").strip()
        if len(m) > 120 or any(c in m for c in "\r\n\t\""):
            raise aiclient.AiError("no_settings", "نام مدل نامعتبر است")
        clean["model"] = m
    if "key_env" in body:
        e = str(body["key_env"] or "").strip()
        if len(e) > 64 or not e.replace("_", "").isalnum():
            raise aiclient.AiError("no_settings", "نام متغیر کلید نامعتبر است")
        clean["key_env"] = e
    return clean

# -------------------------------------------------------------- merge-save ---
def _save_fields(clean: dict) -> dict | None:
    """ادغام فیلدهای مجاز در config.json؛ بقیه دست‌نخورده می‌مانند.
    config خوانا نبود → None (خطا)."""
    with _LOCK:
        f, data = _read_config()
        if data is None:
            return None
        ai = dict(data.get("ai") or {})
        if "provider" in clean:
            ai["provider"] = clean["provider"]
        if "enabled" in clean:
            ai["enabled"] = clean["enabled"]
        secname = ai.get("provider") or "openai"
        sec = dict(ai.get(secname) or {})
        for k in ("base_url", "model", "key_env"):
            if k in clean:
                sec[k] = clean[k]
        ai[secname] = sec
        data["ai"] = ai
        _write_config(f, data)
    return {"ok": True, "saved": True}

# ----------------------------------------------------------------- storage ---
def _key_path() -> Path:
    return _data_dir() / _KEY_FILE


def key_state() -> str:
    """«set» فقط وقتی فایل هست؛ مقدار هرگز برنمی‌گردد."""
    return "set" if _key_path().is_file() else "unset"


def _register_key_path() -> bool:
    """مسیر فایل کلید (نه مقدارش) را در بخش provider فعلی ثبت می‌کند."""
    with _LOCK:
        f, data = _read_config()
        if data is None:
            return False
        ai = dict(data.get("ai") or {})
        secname = ai.get("provider") or "openai"
        sec = dict(ai.get(secname) or {})
        sec["api_key_file"] = _KEY_FILE_REL
        ai[secname] = sec
        data["ai"] = ai
        _write_config(f, data)
        return True


def _write_key_raw(value: str) -> None:
    """فرمت فایل کلید: «فقط متنِ کلید» — دقیقاً همان چیزی که aiclient از
    api_key_file می‌خواند. نوشتن اتمی + 0600 + پوشه data با 0700."""
    d = _data_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    tmp = d / (_KEY_FILE + ".tmp")
    tmp.write_text(value + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, d / _KEY_FILE)


def _set_key(value: str) -> None:
    _write_key_raw(value)
    _register_key_path()


def _delete_key() -> None:
    try:
        _key_path().unlink(missing_ok=True)
    except OSError:
        pass
    with _LOCK:
        f, data = _read_config()
        if data is not None:
            ai = data.get("ai")
            if isinstance(ai, dict):
                for sec in ai.values():
                    if isinstance(sec, dict):
                        sec.pop("api_key_file", None)
                _write_config(f, data)

# --------------------------------------------------------------- endpoints ---
def route(method: str, path: str, body=None, *, cookies: str = "",
          headers: dict | None = None):
    """مسیریاب API. خروجی: (http_code, payload_dict, [Set-Cookie strings])."""
    method = (method or "GET").upper()
    p = (path or "").split("?", 1)[0].strip("/")
    if p.startswith("api/ai/"):
        p = p[len("api/ai/"):]
    elif p.startswith("ai/"):
        p = p[len("ai/"):]
    headers = headers or {}

    public = {"status", "admin/settings", "admin/csrf"}

    # ---- GET endpoints ----
    if method == "GET":
        if p == "status":
            res = aiclient.status(fresh_ping=True)
            ai_cfg, _ = _ai_view()
            res = dict(res, enabled=ai_cfg.get("enabled", False))
            return 200, _guard(res), []
        if p == "admin/settings":
            return 200, _guard(_settings_payload()), []
        if p == "admin/csrf":
            tok = csrf_token()
            return 200, {"ok": True, "csrf": tok}, [
                "%s=%s; Path=/; SameSite=Strict" % (_CSRF_COOKIE, tok)]
        return _bad("یافت نشد", 404)

    # ---- POST endpoints (همه با CSRF؛ همه به‌جز init با مدیر) ----
    if method == "POST":
        if p not in {"admin/init", "admin/save", "admin/key",
                     "admin/key/delete", "admin/test"}:
            return _bad("یافت نشد یا متد مجاز نیست", 404)
        if not _check_csrf(cookies, headers):
            return _bad("توکن CSRF نامعتبر است؛ صفحه را دوباره باز کنید", 403)
        record = {}
        if p == "admin/init":
            code, res, cookies2 = _ep_init(body)
            return code, res, cookies2
        if not verify_admin(headers):
            return _bad("دسترسی مدیریتی لازم است (رمز مدیر درست نیست)", 403)
        if p == "admin/save":
            code, res, cookies2 = _ep_save(body)
            return code, res, cookies2
        if p == "admin/key":
            code, res, cookies2 = _ep_key(body)
            return code, res, cookies2
        if p == "admin/key/delete":
            code, res, cookies2 = _ep_key_delete()
            return code, res, cookies2
        if p == "admin/test":
            code, res, cookies2 = _ep_test(body)
            return code, res, cookies2
    return _bad("متد مجاز نیست", 405)

# -------------------------------------------------------- endpoint internals -
def _guard(payload: dict) -> dict:
    return dict(payload, ok=True)


def _ai_view() -> tuple[dict, dict]:
    """بخش ai از config پروژه (فقط فیلدهای غیرمحرمانه)."""
    _, data = _read_config()
    data = data or {}
    ai = data.get("ai") or {}
    return ai, data


def _settings_payload() -> dict:
    ai, _ = _ai_view()
    secname = ai.get("provider") or "openai"
    sec = ai.get(secname) or {}
    lt = _read_private(_ADMIN_FILE).get("last_test")
    return {
        "initialized": admin_initialized(),
        "enabled": ai.get("enabled", False),
        "provider": ai.get("provider") or "openai",
        "providers": aiclient.providers(),
        "base_url": sec.get("base_url") or "",
        "model": sec.get("model") or "",
        "key_env": sec.get("key_env") or "AI_API_KEY",
        "key": key_state(),                    # فقط set/unset — هرگز مقدار
        "key_file": sec.get("api_key_file") or "",
        "last_test": lt if isinstance(lt, dict) else None,
    }


def _ep_init(body):
    if admin_initialized():
        return 409, {"ok": False,
                     "error": "رمز مدیر قبلاً تنظیم شده؛ bootstrap مجدد ممکن نیست"}, []
    pc = str((body or {}).get("passcode") or "")
    if len(pc) < _PASS_MIN:
        return _bad("رمز مدیر باید حداقل %d نویسه باشد" % _PASS_MIN)
    salt = secrets.token_bytes(16)
    rec = {"salt": salt.hex(), "iterations": _ADMIN_PASS_ITERATIONS,
           "hash": _pass_hash(pc, salt, _ADMIN_PASS_ITERATIONS).hex(),
           "created": datetime.now().isoformat(timespec="seconds"),
           "last_test": None}
    _write_private(_ADMIN_FILE, rec)
    return 200, _guard({"initialized": True,
                        "note": "رمز ذخیره شد (فقط salt/hash؛ خود رمز هرگز)"}), []


def _ep_save(body):
    try:
        clean = validate_settings(body or {})
    except aiclient.AiError as e:
        return _bad(e.message)
    if _save_fields(clean) is None:
        return _bad("config.json خوانا نیست؛ اصلاح دستی لازم است", 500)
    return 200, _guard(_settings_payload()), []


def _ep_key(body):
    val = str((body or {}).get("api_key") or "").strip()
    if not val or len(val) > 512:
        return _bad("مقدار کلید خالی است یا طولش از حد مجاز بیرون است")
    _set_key(val)
    return 200, _guard({"key": "set", "key_file": _KEY_FILE_REL,
                        "note": "کلید فقط سمت سرور در data/ai_key.txt (0600) ذخیره شد"}), []


def _ep_key_delete():
    _delete_key()
    return 200, _guard({"key": "unset"}), []


def _ep_test(body):
    """تست واقعی با همان منطق provider (aiclient.test_connection). overrides
    اختیاری {provider, base_url, model} برای «تست قبل از ذخیره» — اعتبارسنجی
    کامل، سپس env موقت (و آلفای بلافاصله)."""
    ai, _ = _ai_view()
    if not ai.get("enabled", False):
        return 200, _guard({"ready": False, "disabled": True,
                            "detail": "قابلیت هوش مصنوعی غیرفعال است"}), []
    ov = {}
    try:
        clean = validate_settings(body or {})
        for k in ("provider", "base_url", "model"):
            if k in clean:
                ov["AI_" + k.upper()] = clean[k]
    except aiclient.AiError as e:
        return _bad(e.message)
    saved = {k: os.environ[k] for k in ov if k in os.environ}
    try:
        for k, v in ov.items():
            os.environ[k] = v
        try:
            res = aiclient.test_connection(timeout=8)
        except aiclient.AiError as e:
            res = {"ready": False, "detail": e.message}
    finally:
        for k in ov:
            if k in saved:
                os.environ[k] = saved[k]
            else:
                os.environ.pop(k, None)
    with _LOCK:
        st = _read_private(_ADMIN_FILE)
        if st:
            st["last_test"] = {"ts": datetime.now().isoformat(timespec="seconds"),
                               "ready": bool(res.get("ready")),
                               "detail": str(res.get("detail") or "")[:200],
                               "model": str(res.get("model") or ov.get("AI_MODEL") or "")[:120]}
            _write_private(_ADMIN_FILE, st)
    return 200, _guard(res), []

# ------------------------------------------------------- server integration --
def _send(handler, code: int, payload, cookies: list[str]) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        handler.send_response(code)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(raw)))
        for c in cookies or []:
            handler.send_header("Set-Cookie", c)
        handler.end_headers()
        handler.wfile.write(raw)
    except (BrokenPipeError, ConnectionResetError):
        pass


def handle(handler) -> None:
    """تمام glue برای BaseHTTPRequestHandler (API + صفحه‌ی تنظیمات)."""
    path = (handler.path or "").split("?", 1)[0]
    method = (handler.command or "GET").upper()
    if path in UI_FILES:
        ctype, fpath = UI_FILES[path]
        try:
            raw = fpath.read_bytes()
        except OSError:
            handler.send_response(404); handler.end_headers()
            return
        try:
            handler.send_response(200)
            handler.send_header("Content-Type", ctype)
            handler.send_header("Content-Length", str(len(raw)))
            handler.send_header("Cache-Control", "no-store")
            handler.end_headers()
            handler.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass
        return
    n = int(handler.headers.get("Content-Length") or 0)
    body = None
    if n:
        try:
            body = json.loads(handler.rfile.read(n).decode("utf-8") or "null")
        except ValueError:
            return _send(handler, 400, {"ok": False, "error": "بدنه نامعتبر است"},
                         [])
        if body is not None and not isinstance(body, dict):
            return _send(handler, 400, {"ok": False,
                                        "error": "بدنه باید object JSON باشد"}, [])
    cookies = handler.headers.get("Cookie") or ""
    headers = {k: (handler.headers.get(k) or "")
               for k in ("X-AI-Admin", "X-AI-CSRF")}
    code, payload, set_c = route(method, path, body,
                                 cookies=cookies, headers=headers)
    _send(handler, code, payload, set_c)
