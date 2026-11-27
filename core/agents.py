"""P7-1 — Agent Registry: persistent, validated agent definitions.

Each agent is ONE JSON file in ``agents/`` (dir already 0700):

    {
      "name": "builder",              # slug — immutable identity
      "role": "سازنده",                # Persian display label
      "description": "...",            # what it is for (optional)
      "instructions": "...",           # behaviour contract for the model
      "model": "provider/model",       # optional model override
      "max_turns": 6,                  # turn budget for the orchestrator
      "allowed_types": ["webpage"],    # project types it may build (null=all)
      "enabled": true,                 # registry on/off switch
      "created": "...", "modified": "..."
    }

Rules:
  * NO secrets are ever accepted or stored (schema rejects unknown fields —
    an ``api_key``/``token`` field is a hard validation error).
  * Writes are atomic (tmp + os.replace), single-writer under a module lock.
  * The registry NEVER talks to the model; it is pure configuration.
"""

import json
import re
import threading
from datetime import datetime
from pathlib import Path

import core.config as config

_lock = threading.RLock()

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,60}/[A-Za-z0-9._-]{1,60}$")

# exact schema — unknown keys are REJECTED (no free-form, no secrets)
_FIELDS = {"name", "role", "description", "instructions", "model",
           "max_turns", "allowed_types", "enabled", "created", "modified"}
_SECRET_HINTS = ("key", "token", "secret", "password", "passwd")

_DEFAULT_AGENT = {
    "name": "builder",
    "role": "سازنده",
    "description": "عامل پیش‌فرض ساخت: درخواست را به پروژهٔ کامل تبدیل می‌کند",
    "instructions": (
        "تو «سازندهٔ» کارخانهٔ نرم‌افزار هستی. درخواست کاربر را به یک پروژهٔ "
        "کامل و اجراپذیر تبدیل کن: فایل‌های حیاتی را اول بساز، کد را کامل و "
        "بدون placeholder بنویس، و در پاسخ فقط کارهای انجام‌شده را خلاصه کن. "
        "فارسی و RTL را در همهٔ متن‌های رابط کاربری رعایت کن."),
    "model": "",
    "max_turns": 6,
    "allowed_types": None,
    "enabled": True,
}


class AgentError(Exception):
    def __init__(self, status: int, message: str, errors: list | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.errors = errors or []


def _dir() -> Path:
    d = config.AGENTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError:
        pass
    return d


def name_valid(name: str) -> bool:
    return bool(_NAME_RE.match(name or ""))


def _path(name: str) -> Path:
    if not name_valid(name):
        raise AgentError(400, "نام عامل نامعتبر است (حروف کوچک لاتین، رقم، - و _)")
    return _dir() / f"{name}.json"


def _read(p: Path) -> dict:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(p: Path, data: dict) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(p)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------- validation ---

def validate(data: dict, *, for_create: bool = False) -> list[str]:
    """Return a list of Persian validation errors (empty = valid)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["بدنه باید یک شیء JSON باشد"]
    unknown = sorted(set(data) - _FIELDS)
    if unknown:
        errors.append("فیلدهای ناشناخته مجاز نیستند: " + ", ".join(unknown))
        for f in unknown:
            if any(h in f.lower() for h in _SECRET_HINTS):
                errors.append(f"ذخیرهٔ اسرار در تعریف عامل مطلقاً ممنوع است: {f}")
    if for_create or "name" in data:
        if not name_valid(str(data.get("name", ""))):
            errors.append("نام باید ۲ تا ۳۲ کاراکتر از [a-z0-9_-] باشد و با رقم یا حرف شروع شود")
    role = data.get("role", "")
    if for_create or "role" in data:
        if not isinstance(role, str) or not role.strip():
            errors.append("«نقش» لازم است")
        elif len(role) > 80:
            errors.append("«نقش» حداکثر ۸۰ کاراکتر")
    instr = data.get("instructions", "")
    if for_create or "instructions" in data:
        if not isinstance(instr, str) or len(instr.strip()) < 10:
            errors.append("«دستورالعمل» لازم است (حداقل ۱۰ کاراکتر)")
        elif len(instr) > 8000:
            errors.append("«دستورالعمل» حداکثر ۸۰۰۰ کاراکتر")
    model = data.get("model", "")
    if model:
        if not isinstance(model, str) or not _MODEL_RE.match(model):
            errors.append("قالب مدل باید «providerID/modelID» باشد")
    turns = data.get("max_turns", 6)
    if not isinstance(turns, int) or isinstance(turns, bool) \
            or not (1 <= turns <= 20):
        errors.append("max_turns باید عدد صحیح ۱ تا ۲۰ باشد")
    types = data.get("allowed_types", None)
    if types is not None:
        if not isinstance(types, list) or not all(
                isinstance(t, str) and t for t in types):
            errors.append("allowed_types باید فهرست رشته یا null باشد")
        else:
            from core.factory import TYPES
            bad = [t for t in types if t not in TYPES]
            if bad:
                errors.append("نوع‌های ناشناخته: " + ", ".join(bad))
    if "enabled" in data and not isinstance(data["enabled"], bool):
        errors.append("enabled باید true/false باشد")
    return errors


# ----------------------------------------------------------------- CRUD ------

def list_agents() -> list[dict]:
    with _lock:
        _seed_if_empty()
        out = []
        for p in sorted(_dir().glob("*.json")):
            if p.name.endswith(".tmp"):
                continue
            a = _read(p)
            if a:
                out.append(a)
        out.sort(key=lambda a: a.get("name", ""))
        return out


def _seed_if_empty() -> None:
    """Create the default «builder» agent exactly once, explicitly, when the
    registry is empty. This is configuration (no credential of any kind)."""
    d = _dir()
    if any(d.glob("*.json")):
        return
    seed = dict(_DEFAULT_AGENT)
    seed["created"] = seed["modified"] = _now()
    _write(d / "builder.json", seed)


def get_agent(name: str) -> dict:
    with _lock:
        a = _read(_path(name))
    if not a:
        raise AgentError(404, "عامل یافت نشد: " + name)
    return a


def create_agent(data: dict) -> dict:
    errors = validate(data, for_create=True)
    if errors:
        raise AgentError(400, "تعریف عامل نامعتبر است", errors)
    with _lock:
        p = _path(str(data["name"]))
        if p.exists():
            raise AgentError(409, "عامل هم‌نام از قبل وجود دارد: " + data["name"])
        rec = {"role": "", "description": "", "instructions": "",
               "model": "", "max_turns": 6, "allowed_types": None,
               "enabled": True}
        for k in ("name", "role", "description", "instructions", "model",
                  "max_turns", "allowed_types", "enabled"):
            if k in data:
                rec[k] = data[k]
        rec["created"] = rec["modified"] = _now()
        _write(p, rec)
    return rec


def update_agent(name: str, data: dict) -> dict:
    if "name" in data and data["name"] != name:
        raise AgentError(400, "تغییر نام عامل مجاز نیست؛ عامل جدید بسازید")
    with _lock:
        p = _path(name)
        rec = _read(p)
        if not rec:
            raise AgentError(404, "عامل یافت نشد: " + name)
        patch = {k: v for k, v in data.items() if k in _FIELDS
                 and k not in ("name", "created", "modified")}
        # P7-1: strict update — the RAW body is validated, so unknown fields
        # (or secret-looking keys) are rejected instead of silently dropped.
        errors = validate(data)
        if errors:
            raise AgentError(400, "به‌روزرسانی نامعتبر است", errors)
        rec.update(patch)
        rec["modified"] = _now()
        _write(p, rec)
    return rec


def delete_agent(name: str) -> dict:
    with _lock:
        p = _path(name)
        if not p.exists():
            raise AgentError(404, "عامل یافت نشد: " + name)
        p.unlink()
    return {"deleted": name}


def enabled_agents() -> list[dict]:
    return [a for a in list_agents() if a.get("enabled")]


def resolve_model(agent: dict) -> str:
    """Model override for an agent ('' = orchestrator/panel default)."""
    m = str(agent.get("model") or "").strip()
    return m if _MODEL_RE.match(m) else ""


def alternative_model(agent_name: str) -> str:
    """AF-47 — a REAL connected/enabled alternative model, or ''.

    Never assumes a provider exists: the list comes from the live OpenCode
    bridge, and the current agent's model is excluded.
    """
    from core.model_gateway import gateway
    current = ""
    try:
        current = resolve_model(get_agent(agent_name))
    except AgentError:
        pass
    try:
        models = gateway().list_models()
    except Exception:  # noqa: BLE001 — no verified list means no fallback
        return ""
    for m in models:
        mid = "%s/%s" % (m.get("providerID") or "", m.get("modelID") or "")
        if (m.get("active") and m.get("enabled") and mid != current and
                _MODEL_RE.match(mid)):
            return mid
    return ""
