"""aiclient — اتصال استاندارد و مستقل نرم‌افزار تولیدشده به هوش مصنوعی.

این فایل توسط اسکفولدر AI Factory (core.scaffold.scaffold_ai_module) تنها برای
پروژه‌های AI‌دار کپی می‌شود. عمداً «خالص و مستقل» است:

* فقط کتابخانه استاندارد پایتون — بدون pip، بدون OpenCode، بدون AI Factory.
* ارائه‌دهنده (Provider) قابل تعویض است؛ منطق برنامه فقط `send` را صدا می‌زند
  و هرگز مستقیماً به داخل ارائه‌دهنده اشاره نمی‌کند. ارائه‌دهنده‌های جدید صرفاً
  با `register_provider` اضافه می‌شوند.
* کلید/توکن فقط سمت سرور: از متغیر محیطی یا فایل کلید خوانده می‌شود؛ هرگز در
  config ذخیره نمی‌شود، لاگ نمی‌شود، در استثنا/پاسخ/پنل برمی‌گردد.
* `ai` بخش config.json فقط تنظیمات غیرمحرمانه (provider/base_url/model/key_env).

راه‌های تنظیم (اولویت از راست به چپ: env > config.json > پیش‌فرض):
  AI_PROVIDER              نام ارائه‌دهنده (پیش‌فرض openai)
  AI_BASE_URL              آدرس سرویس سازگار OpenAI مثل https://…/v1
  AI_MODEL                 نام مدل مثل gpt-4o-mini
  AI_API_KEY یا <key_env>  کلید سمت سرور
  AI_API_KEY_FILE          مسیر فایل کلید (فقط سمت سرور)
  AI_TIMEOUT               ثانیه (پیش‌فرض 60)

نقطه توسعه P3 (انجام‌شده):  describe_settings()/apply_settings() برای مدیریت
غیرمحرمانه؛ صفحه‌ی تنظیمات و لایه سیاست در app/ai_admin.py (کنار همین فایل).
"""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

__all__ = ["AiError", "send", "status", "test_connection",
           "describe_settings", "apply_settings", "register_provider",
           "providers"]

# ---------------------------------------------------------------- errors ----
class AiError(Exception):
    """خطای روشن با نوع مشخص؛ پیام‌ها هرگز کلید/توکن در خود ندارند.

    kind یکی از: no_settings | auth | rate_limit | timeout | network |
                 http | bad_response
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message

    def __str__(self):
        return self.message     # عمداً زنجیره استثنای خام برنمی‌گردانیم

# ---------------------------------------------------------------- config ----
def _cfg_root() -> Path:
    """ریشه پروژه (پوشه والد app/) یا مسیر جایگزین با AI_CONFIG_FILE."""
    env = os.environ.get("AI_CONFIG_FILE")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parent.parent      # …/app/aiclient.py → …

def _load_ai_config() -> dict:
    """خواندن بخش غیرمحرمانه ai از config.json/ai.json پروژه."""
    root = _cfg_root()
    for name in ("config.json", "ai.json"):
        p = root / name
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("ai"), dict):
            return data["ai"]
    return {}

def _resolve() -> dict:
    """تنظیمات نهایی؛ کلید فقط اینجا خوانده و فقط برای درخواست استفاده می‌شود."""
    ai = _load_ai_config()
    sec = ai.get(_provider_name(ai)) or {}
    base = (os.environ.get("AI_BASE_URL") or sec.get("base_url") or "").rstrip("/")
    model = (os.environ.get("AI_MODEL") or sec.get("model") or "").strip()
    key_env = (sec.get("key_env") or "AI_API_KEY").strip()
    key = (os.environ.get(key_env) or os.environ.get("AI_API_KEY") or "").strip()
    if not key:
        kf = os.environ.get("AI_API_KEY_FILE") or sec.get("api_key_file") or ""
        if kf and os.path.isfile(kf):
            try:
                key = Path(kf).read_text(encoding="utf-8").strip()
            except OSError:
                key = ""
    try:
        timeout = float(os.environ.get("AI_TIMEOUT") or sec.get("timeout") or 60)
    except (TypeError, ValueError):
        timeout = 60.0
    return {"provider": _provider_name(ai), "base": base, "model": model,
            "key": key, "key_env": key_env, "timeout": timeout}

def _provider_name(ai_cfg: dict) -> str:
    return (os.environ.get("AI_PROVIDER") or ai_cfg.get("provider") or
            "openai").strip().lower()

# ---------------------------------------------------------------- http ------
def _http(method, url, *, body=None, headers=None, timeout=30.0):
    """اجرای ساده HTTP با خطاهای طبقه‌بندی‌شده؛ هیچ متن خام از سرور ارائه‌دهنده
    در پیام خطا نمی‌آید (ممکن است کلید را echo کنند)."""
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, raw
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AiError("auth", "کلید پذیرفته نشد (HTTP %d)" % e.code) from None
        if e.code == 429:
            ra = e.headers.get("Retry-After") or ""
            msg = "سقف نرخ ارائه‌دهنده (HTTP 429)"
            if ra:
                msg += " — تلاش مجدد پس از %s ثانیه" % ra
            raise AiError("rate_limit", msg) from None
        raise AiError("http", "ارائه‌دهنده پاسخ HTTP %d داد" % e.code) from None
    except TimeoutError as e:
        raise AiError("timeout", "پاسخ ارائه‌دهنده به‌موقع نیامد") from None
    except (urllib.error.URLError, ConnectionError, OSError) as e:
        # فقط «عبارت صریح» اتصال؛ عناصر خام (ممکن است آدرس خصوصی داشته باشند)
        raise AiError("network", "اتصال به سرویس برقرار نشد") from None

# ------------------------------------------------------- built-in provider --
def _openai_send(cfg: dict, messages: list, *, model=None,
                 temperature: float = 0.2, timeout=None) -> str:
    """ارائه‌دهنده سازگار OpenAI (/chat/completions) — آداپتور کامل."""
    missing = [n for n, v in (("AI_BASE_URL/base_url", cfg["base"]),
                              ("AI_MODEL/model", cfg["model"]),
                              ("کلید (" + cfg["key_env"] + ")",
                               cfg["key"])) if not v]
    if missing:
        raise AiError("no_settings",
                      "تنظیمات هوش مصنوعی کامل نیست: " + " و ".join(missing))
    payload = {
        "model": (model or cfg["model"]),
        "messages": messages,
        "temperature": temperature,
    }
    headers = {}
    if cfg["key"]:
        headers["Authorization"] = "Bearer " + cfg["key"]
    t = timeout if timeout is not None else cfg["timeout"]
    st, raw = _http("POST", cfg["base"] + "/chat/completions",
                    body=payload, headers=headers, timeout=t)
    if st != 200:
        raise AiError("http", "ارائه‌دهنده پاسخ HTTP %d داد" % st)
    try:
        parsed = json.loads(raw)
        content = parsed["choices"][0]["message"]["content"]
        return content or ""
    except (ValueError, KeyError, IndexError, TypeError):
        raise AiError("bad_response", "پاسخ ارائه‌دهنده قابل خواندن نبود") from None

def _openai_ping(cfg: dict) -> dict:
    """تست اتصال واقعی به GET /models (سبک و بدون پاسخ مدل)."""
    if not cfg["base"]:
        return {"ready": False, "detail": "آدرس سرویس (AI_BASE_URL) تنظیم نشده"}
    if not cfg["key"]:
        return {"ready": False, "detail": "کلید تنظیم نشده (" + cfg["key_env"] + ")"}
    headers = {"Authorization": "Bearer " + cfg["key"]}
    t = min(cfg["timeout"], 6.0)
    st, raw = _http("GET", cfg["base"] + "/models", headers=headers, timeout=t)
    if st != 200:
        return {"ready": False,
                "detail": "سرویس پاسخ HTTP %d داد" % st}
    try:
        parsed = json.loads(raw)
        ids = [m.get("id") for m in (parsed.get("data") or []) if m.get("id")]
    except ValueError:
        return {"ready": False, "detail": "پاسخ سرویس قابل خواندن نبود"}
    if cfg["model"] and cfg["model"] not in ids:
        return {"ready": False, "model": cfg["model"], "models": ids[:25],
                "detail": "مدل «%s» در فهرست سرویس نیست" % cfg["model"]}
    return {"ready": True, "model": cfg["model"], "models": ids[:25],
            "detail": "اتصال واقعی برقرار است"}

# ------------------------------------------------------------- registry -----
_PROVIDERS: dict = {}

def register_provider(name: str, *, send, ping=None) -> dict:
    """افزودن ارائه‌دهنده جدید بدون تغییر منطق برنامه (نقطه توسعه).

    send(cfg, messages, *, model=None, temperature=0.2, timeout=None) -> str
    ping(cfg) -> {"ready": bool, "detail": str, ...}
    """
    name = name.strip().lower()
    _PROVIDERS[name] = {"send": send, "ping": ping}
    return dict(_PROVIDERS)        # کپی؛ رجیستری واقعی را نمی‌دهد

register_provider("openai", send=_openai_send, ping=_openai_ping)

def providers() -> list:
    """نام ارائه‌دهنده‌های ثبت‌شده (برای صفحه تنظیمات/P3)."""
    return sorted(_PROVIDERS)


# ------------------------------------------------------------- public API ---
def send(messages: list, *, model: str = None, temperature: float = 0.2,
         timeout: float = None) -> str:
    """تنهام گذرگاه نرم‌افزار برای گفتگو با هوش مصنوعی.

    messages: [{"role": "system"/"user"/"assistant", "content": "…"}, …]
    خروجی: متن پاسخ. خطا: AiError با kind مشخص.
    """
    if not isinstance(messages, list) or not messages:
        raise AiError("bad_response", "messages نامعتبر است")
    cfg = _resolve()
    spec = _PROVIDERS.get(cfg["provider"])
    if spec is None:
        envp = os.environ.get("AI_PROVIDER")
        if envp:
            # ارائه‌دهنده ثبت نشده — تلاش مجدد از طریق ارائه‌دهنده‌های پلاگین
            raise AiError(
                "no_settings",
                "ارائه‌دهنده «%s» ثبت نشده است" % envp)
        spec = _PROVIDERS["openai"]
    return spec["send"](cfg, messages, model=model,
                        temperature=temperature, timeout=timeout)

def status(fresh_ping: bool = True) -> dict:
    """وضعیت اتصال برای نمایش؛ هرگز شامل کلید نیست."""
    cfg = _resolve()
    spec = _PROVIDERS.get(cfg["provider"])
    info = {"provider": cfg["provider"], "ready": False,
            "base_url": cfg["base"] or "", "model": cfg["model"] or "",
            "key": "set" if cfg["key"] else "unset"}
    if spec is None:
        info["detail"] = "ارائه‌دهنده ثبت نشده است"
        return info
    if fresh_ping and spec.get("ping"):
        try:
            ping = spec["ping"](cfg)
        except AiError as e:
            info["detail"] = e.message
            return info
        info.update(ping)
        info.setdefault("detail", "آماده نیست")
        return info
    # بدون ping: فقط صحت تنظیمات
    ready = bool(cfg["base"] and cfg["model"] and cfg["key"])
    info.update({"ready": ready,
                 "detail": "تنظیمات کامل است" if ready else "تنظیمات کامل نیست"})
    return info

def test_connection(timeout: float = None) -> dict:
    """تست اتصال صریح (برای دکمه آینده تنظیمات/P3). کوچک و بدون مدل."""
    cfg = _resolve()
    if timeout:
        cfg = dict(cfg, timeout=min(float(timeout), cfg["timeout"]))
    return status(fresh_ping=True)

def describe_settings() -> dict:
    """فیلدهای قابل ویرایش بدون هیچ مقدار محرمانه — نقطه اتصال P3."""
    cfg = _resolve()
    return {"provider": cfg["provider"], "base_url": cfg["base"],
            "model": cfg["model"], "key_env": cfg["key_env"],
            "note": "کلید فقط با متغیر محیطی یا فایل کلید تنظیم می‌شود"}

def apply_settings(settings: dict, *, path: str = None) -> dict:
    """ذخیره تنظیمات غیرمحرمانه در config.json (نگارش محافظت‌شده).

    فقط این کلیدها پذیرفته می‌شود: provider، base_url، model، key_env.
    اگر کلید/توکن/… تنظیم باشد رد می‌شود (سوالی از افشا چیزی ذخیره نمی‌شود).
    """
    allowed = {"provider", "base_url", "model", "key_env"}
    clean = {k: str(v).strip()[:400] for k, v in (settings or {}).items()
             if k in allowed}
    rejected = sorted(set(settings or {}) - allowed)
    if rejected:
        raise AiError("no_settings",
                      "کلیدهای مجاز نیستند: " + ", ".join(rejected))
    root = Path(path) if path else _cfg_root()
    fname = root / "config.json"
    data = {}
    if fname.is_file():
        try:
            data = json.loads(fname.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    if not isinstance(data, dict):
        data = {}
    data["ai"] = {"provider": clean.get("provider", "openai"),
                  "openai": {"base_url": clean.get("base_url", ""),
                             "model": clean.get("model", ""),
                             "key_env": clean.get("key_env", "AI_API_KEY")}}
    fname.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    return {"saved": True, "path": str(fname), "note": "کلید ذخیره نشد"}

# ---------------------------------------------------------------- end -------
