"""AF NEXT Phase 02 — canonical Request Intelligence and Planning layer.

Transforms untrusted natural-language request text into one versioned,
reviewable request record BEFORE any specialist execution.  This module owns
classification, clarification, planning and approval routing; it does NOT
execute specialists, generate products, or call providers.

Design constraints (see docs/rebuild/DECISIONS.md, ADR-008):
  * product DOMAIN and delivery PLATFORM are independent dimensions;
  * intent/risk/complexity are deterministic, explainable and testable;
  * user text is untrusted data and can never grant permission;
  * approvals are bound to exact request revision/hash, plan hash, actor and
    one-shot token, and fail closed on any mismatch or replay;
  * persistence uses an flock-guarded atomic write, preserving prior records.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import core.config as config

try:  # POSIX (Termux/Linux) target; non-POSIX narrows the guarantee, fails closed
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

SCHEMA_VERSION = "af-request-1.0"
STORE_VERSION = "af-request-store-1"
_MAX_REQUESTS = 400

STATES = (
    "RECEIVED", "CLASSIFIED", "NEEDS_CLARIFICATION", "READY_FOR_PLAN",
    "PLANNED", "AWAITING_APPROVAL", "APPROVED", "REJECTED", "BLOCKED",
)
TRANSITIONS = {
    "RECEIVED": ("CLASSIFIED", "REJECTED", "BLOCKED"),
    "CLASSIFIED": ("NEEDS_CLARIFICATION", "READY_FOR_PLAN", "REJECTED", "BLOCKED"),
    "NEEDS_CLARIFICATION": ("CLASSIFIED", "READY_FOR_PLAN", "REJECTED", "BLOCKED"),
    "READY_FOR_PLAN": ("PLANNED", "NEEDS_CLARIFICATION", "REJECTED", "BLOCKED"),
    "PLANNED": ("AWAITING_APPROVAL", "APPROVED", "READY_FOR_PLAN",
                "REJECTED", "BLOCKED"),
    "AWAITING_APPROVAL": ("APPROVED", "REJECTED", "READY_FOR_PLAN", "BLOCKED"),
    "APPROVED": ("REJECTED", "BLOCKED"),
    "REJECTED": (),
    "BLOCKED": ("CLASSIFIED", "NEEDS_CLARIFICATION", "READY_FOR_PLAN", "REJECTED"),
}

INTENTS = ("create", "modify", "debug", "explain", "analyze", "plan",
           "research", "deploy", "other")
RISK_LEVELS = ("low", "medium", "high", "critical")
COMPLEXITY_LEVELS = ("small", "medium", "large", "very-large", "unknown")
PLATFORMS = ("Android", "Web", "Desktop", "Cross-platform", "unknown")
DOMAINS = ("game", "education", "business", "automation", "content",
           "general", "unknown")

_STORE = config.PROJECT_ROOT / "data" / "canonical_requests.json"
_FLAGS = config.PROJECT_ROOT / "data" / "request_intelligence_flags.json"
_LOCK = threading.RLock()

_NORM = {"ك": "ک", "ي": "ی", "ؤ": "و", "إ": "ا", "أ": "ا", "ة": "ه",
         "0": "۰", "1": "۱", "2": "۲", "3": "۳", "4": "۴",
         "5": "۵", "6": "۶", "7": "۷", "8": "۸", "9": "۹"}


class RequestStoreError(RuntimeError):
    """Raised on corrupted/unavailable request storage or invalid transitions."""


class RequestError(RuntimeError):
    """Explicit, user-facing failure (approval, clarification, validation)."""


# ---------------------------------------------------------------------------
# Persistence — flock guarded, marker distinguishes genuinely empty from lost
# ---------------------------------------------------------------------------

def _unique_tmp(path: Path) -> Path:
    return Path(str(path) + ".%d.%d.%s.tmp" %
                (os.getpid(), threading.get_ident(), secrets.token_hex(6)))


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _unique_tmp(path)
    try:
        tmp.write_text(text, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _marker() -> Path:
    return Path(str(_STORE) + ".initialized")


@contextmanager
def _request_lock():
    with _LOCK:
        if fcntl is None:
            raise RequestStoreError("request-lock-unavailable:fcntl-missing")
        lock_path = Path(str(_STORE) + ".lock")
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            raise RequestStoreError("request-lock-open-failed:%s" %
                                    type(exc).__name__) from exc
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError as exc:
                raise RequestStoreError("request-lock-acquire-failed:%s" %
                                        type(exc).__name__) from exc
            try:
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            os.close(fd)


def _load_unlocked() -> dict:
    try:
        text = _STORE.read_text(encoding="utf-8")
    except FileNotFoundError:
        if _marker().exists():
            raise RequestStoreError("request-store-missing-after-init")
        return {}
    except OSError as exc:
        raise RequestStoreError("request-store-unreadable:%s" %
                                type(exc).__name__) from exc
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        raise RequestStoreError("request-store-corrupt-json")
    if not isinstance(data, dict) or data.get("schema") != STORE_VERSION or \
            not isinstance(data.get("items"), dict):
        raise RequestStoreError("request-store-invalid-schema")
    items = data["items"]
    for key, rec in items.items():
        if not isinstance(rec, dict) or rec.get("request_id") != key:
            raise RequestStoreError("request-store-invalid-entry")
    return items


def _save_unlocked(items: dict) -> None:
    keep = dict(list(items.items())[-_MAX_REQUESTS:])
    try:
        _atomic_write(_STORE, json.dumps(
            {"schema": STORE_VERSION, "items": keep},
            ensure_ascii=False, indent=1, sort_keys=True))
        if not _marker().exists():
            _atomic_write(_marker(), STORE_VERSION + "\n")
    except OSError as exc:
        raise RequestStoreError("request-store-write-failed:%s" %
                                type(exc).__name__) from exc


def _mutate(request_id: str, fn) -> dict:
    """Apply fn under one cross-process lock; fn receives (items, existing)."""
    with _request_lock():
        items = _load_unlocked()
        old = items.get(request_id)
        new = fn(dict(old) if old else None)
        if new is None:
            raise RequestError("request-not-found:" + request_id)
        items[request_id] = new
        _save_unlocked(items)
        return new


# ---------------------------------------------------------------------------
# Deterministic text intelligence
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    out = str(text or "")
    for k, v in _NORM.items():
        out = out.replace(k, v)
    return out


_INTENT_PATTERNS = {
    "create": r"بساز|بسازید|بسازه|ایجاد|ساختن|ساخت|اضافه|پیاده\s*سازی|create|build|new\b|implement",
    "modify": r"تغییر|ویرایش|اصلاح|بهبود|modify|edit|change|update|refactor",
    "debug": r"رفع\s*(?:مشکل|خطا|اشکال|بگ)|دیباگ|درست\s*کن|خطا|اشکال|بگ\s*(?:کن|زده)|fix|debug|bug|error",
    "explain": r"توضیح|چرا|چگونه|چیست|explain|describe|what\s+is|how\s+does",
    "analyze": r"تحلیل|بررسی|ارزیابی|analyze|analysis|review",
    "plan": r"برنامه|طرح\s*(?:ریزی|دهی)|نقشه|plan|roadmap",
    "research": r"تحقیق|پژوهش|جستجو|research|investigate",
    "deploy": r"استقرار|انتشار|بارگذاری|deploy|release|publish|push\s+to",
}

_DOMAIN_PATTERNS = {
    "game": r"بازی|گیم|game|بازیکن|player|امتیاز|score|مرحله\s*بازی|level",
    "education": r"آموزش|یادگیری|درس|دانش\s*آموز|معلم|student|teacher|education|quiz|امتحان",
    "business": r"کسب\s*و\s*کار|فروشگاه|shop|سفارش|مشتری|فاکتور|business|invoice|پرداخت",
    "automation": r"اتوماسیون|خودکار|اسکریپت|automation|workflow|robot|زمان\s*بندی",
    "content": r"محتوا|وبلاگ|مقاله|نوشتار|رسانه|content|blog|article",
}

_PLATFORM_PATTERNS = {
    "Android": r"اندروید|android|apk|aab",
    "Web": r"وب\s*سایت|وب\s*اپ|سایت|html|web|برنامه\s*تحت\s*وب|صفحه\s* وب|وب",
    "Desktop": r"دسکتاپ|desktop|windows|macos|linux|کامپیوتر",
    "Cross-platform": r"چند\s*پلتفرم|cross-?platform|همه\s*پلتفرم|multi-?platform",
}

_RISK_PATTERNS = {
    "destructive": r"rm\s+-rf|wipe|format\s+c|حذف\s*(?:همه|تمام)|پاک\s*کن\s*(?:همه|تمام)|پاکسازی\s*کامل|delete\s+all|همه\s*(?:فایل|داده)|پاکسازی",
    "external_release": r"استقرار|انتشار\s*(?:عمومی|public)?|عمومی|public(?:\s+release)?|deploy|release|publish|push\s+to|ارسال\s*به\s*git|عملیات\s*خارجی|external\s+operation|کاری\s*ناشناخته",
    "financial": r"پرداخت|خرید|پول|هزینه|paid|billing|payment|credit\s*card|تومان",
    "security_sensitive": r"احراز\s*هویت|رمز|پسورد|توکن|secret|credential|auth|دسترسی\s*مطلق|گریز\s*از\s*محدودیت",
    "credentials": r"api[\s_-]*key|توکن[ی]?\s*خودت|\.env|private\s*key| کلید\s*خصوصی",
    "private_data": r"اطلاعات\s*خصوصی|داده\s*های\s*شخصی|personal\s+data|حریم\s*خصوصی|patient|medical",
    "scope_expansion": r"دسترسی\s*مطلق|بالارفتن\s*از|بدون\s*محدودیت\s*صلاحیت|باز\s*کردن\s*تمام\s*فایل|sudo|chmod\s+777",
    "network": r"http[s]?://|curl\b|wget\b|api\s*call|درخواست\s*شبکه",
    "data_loss_migration": r"مهاجرت|migration|انتقال\s*داده|drop\s+table|حذف\s*دیتابیس",
}

_COMPLEXITY_PATTERNS = {
    "multi_component": r"چند\s*(?:بخش|ماژول|سرویس)|فول\s*استک|fullstack|وب\s*سرویس|microservice|backend\s*و\s*frontend",
    "integration": r"اتصال|ادغام|integrat|webhook|سرویس\s*خارجی|third[- ]party",
    "cross_platform": r"چند\s*پلتفرم|cross-?platform|همه\s*پلتفرم",
    "uncertainty": r"نمیدانم|نامشخص|شاید|maybe|نمیدونم|unclear",
}


def _counts(patterns: dict, text: str) -> dict:
    low = text.lower()
    return {k: len(re.findall(p, low)) for k, p in patterns.items()
            if re.search(p, low)}


def classify_intent(text: str) -> dict:
    norm = normalize(text)
    hits = _counts(_INTENT_PATTERNS, norm)
    ranked = sorted(hits.items(), key=lambda kv: (-kv[1], kv[0]))
    if not ranked:
        return {"primary": "other", "secondary": "", "confidence": 0.2,
                "evidence": [], "uncertain": True}
    primary, score = ranked[0]
    secondary = ranked[1][0] if len(ranked) > 1 else ""
    total = sum(hits.values())
    conf = min(0.95, 0.45 + 0.20 * score + (0.10 if total > score else 0))
    return {"primary": primary, "secondary": secondary,
            "confidence": round(conf, 2), "evidence": list(hits),
            "uncertain": conf < 0.55}


def classify_domain(text: str, product_type: str = "") -> dict:
    norm = normalize(text)
    hits = _counts(_DOMAIN_PATTERNS, norm)
    ranked = sorted(hits.items(), key=lambda kv: (-kv[1], kv[0]))
    if ranked:
        domain, score = ranked[0]
        conf = min(0.92, 0.55 + 0.15 * score)
        return {"value": domain, "confidence": round(conf, 2),
                "evidence": list(hits), "unknown": False}
    if product_type in ("shop", "dashboard", "api", "webtool", "webpage",
                        "fullstack", "ai-tool"):
        return {"value": "business" if product_type == "shop" else "general",
                "confidence": 0.45, "evidence": ["product_type:" + product_type],
                "unknown": False}
    return {"value": "unknown", "confidence": 0.2, "evidence": [],
            "unknown": True}


def classify_platform(text: str) -> dict:
    norm = normalize(text)
    hits = _counts(_PLATFORM_PATTERNS, norm)
    values = sorted(hits, key=lambda k: (-hits[k], k))
    if not values:
        return {"value": "unknown", "platforms": [], "confidence": 0.2,
                "evidence": [], "unknown": True}
    if len(values) > 1 and hits[values[0]] == hits[values[1]]:
        chosen = "Cross-platform"
    else:
        chosen = values[0]
    conf = min(0.9, 0.55 + 0.15 * hits[values[0]])
    return {"value": chosen, "platforms": values, "confidence": round(conf, 2),
            "evidence": list(hits), "unknown": False}


def classify_risk(text: str) -> dict:
    norm = normalize(text)
    dims = {}
    for name, pat in _RISK_PATTERNS.items():
        found = re.search(pat, norm, re.I)
        dims[name] = {"present": bool(found),
                      "evidence": found.group(0)[:80] if found else ""}
    critical = any(dims[k]["present"] for k in
                   ("destructive", "credentials", "scope_expansion"))
    high = any(dims[k]["present"] for k in
               ("external_release", "financial", "private_data",
                "security_sensitive", "data_loss_migration"))
    medium = dims["network"]["present"]
    level = ("critical" if critical else "high" if high else
             "medium" if medium else "low")
    reasons = [k for k, v in dims.items() if v["present"]] or ["no-risk-dimension"]
    return {"level": level, "dimensions": dims, "reasons": reasons}


def classify_complexity(text: str) -> dict:
    norm = normalize(text)
    hits = _counts(_COMPLEXITY_PATTERNS, norm)
    words = len(norm.split())
    factors = dict(hits)
    factors["request_length"] = words
    score = len(hits) + (1 if words > 60 else 0) + (1 if words > 140 else 0)
    level = ("small" if score == 0 else "medium" if score <= 2 else
             "large" if score <= 4 else "very-large")
    return {"level": level, "factors": factors, "reasons": list(hits) or
            ["single-component"]}


def extract_requirements(text: str, product_type: str = "") -> list[dict]:
    """Explicit requirements only when directly stated; everything else inferred."""
    norm = normalize(text)
    out = []
    idx = 0

    def add(desc, source, priority="normal", required=True, confidence=None,
            condition="", rationale=""):
        nonlocal idx
        idx += 1
        item = {"id": "req-%02d" % idx, "description": desc, "source": source,
                "priority": priority, "required": bool(required)}
        if confidence is not None:
            item["confidence"] = round(confidence, 2)
        if condition:
            item["acceptance_condition"] = condition
        if rationale:
            item["rationale"] = rationale
        out.append(item)

    # Explicit: split on common separators, keep clauses that look like demands.
    parts = re.split(r"[؛;،,.\n]|and\s+|و\s+همچنین|سپس", norm)
    for part in parts:
        p = part.strip()
        if len(p) < 8 or len(p) > 400:
            continue
        if re.search(r"باید|must|should|لازم|required|داشته\s*باشد|بساز|ایجاد|اضافه|رفع|توضیح|تحلیل|برنامه", p, re.I):
            add(p, "explicit", priority="high", required=True,
                condition="explicit requirement must be verified against the record")
    if not out:
        # A short but clear request still has one explicit goal-level requirement.
        add(norm.strip()[:300] or "incomplete request", "explicit",
            priority="high", required=True)
    # Inferred: never presented as explicit.
    domain = classify_domain(norm, product_type)
    if domain["value"] != "unknown":
        add("پوشش نیازمندیهای رایج دامنهٔ «%s»" % domain["value"], "inferred",
            confidence=domain["confidence"], required=False,
            rationale="domain inference from request keywords")
    plat = classify_platform(norm)
    if plat["value"] != "unknown":
        add("سازگاری با پلتفرم «%s»" % plat["value"], "inferred",
            confidence=plat["confidence"], required=False,
            rationale="platform inference from request keywords")
    add("رعایت اصول امنیتی و محدودیتهای مجوز موجود", "system",
        priority="critical", required=True,
        condition="SecurityPolicy remains final authority; no scope escalation")
    add("حفظ دادههای موجود و جلوگیری از زیان غیرقابل بازگشت", "system",
        priority="critical", required=True,
        condition="no destructive migration; existing records preserved")
    return out


def detect_conflicts(requirements: list[dict], text: str) -> list[dict]:
    norm = normalize(text)
    conflicts = []
    if re.search(r"(?i)(offline|آفلاین|بدون\s*اینترنت)", norm) and \
            re.search(r"(?i)(online|آنلاین|live\s+api|سرویس\s*خارجی)", norm):
        conflicts.append({"id": "conflict-connectivity",
                          "description": "هم آفلاین و هم آنلاین درخواست شده است",
                          "material": True})
    if re.search(r"(?i)(ساده|minimal|کم\s*حجم)", norm) and \
            re.search(r"(?i)(فول\s*استک|fullstack|همه\s*قابلیتها)", norm):
        conflicts.append({"id": "conflict-scope",
                          "description": "سادگی و جامعیت همزمان خواسته شده است",
                          "material": False})
    return conflicts


def essential_missing(text: str, intent: dict, domain: dict, platform: dict,
                      answers: list[dict], product_type: str = "") -> list[dict]:
    norm = normalize(text)
    answered = " ".join(str(a.get("answer") or "") for a in answers)
    answered_keys = {str(a.get("question_id") or "") for a in answers
                     if a.get("resolved") is not False}
    missing = []

    def need(key, why, options, essential=True):
        if key in answered_keys or key in answered.lower() or key in norm.lower():
            return
        missing.append({"id": key, "essential": essential, "why": why,
                        "options": options})

    if intent["primary"] == "other" or intent["uncertain"]:
        need("intent", "نوع کار مشخص نیست و گام بعدی را تغییر میدهد",
             ["ساخت", "تغییر", "رفع مشکل", "توضیح"])
    if intent["primary"] in ("create", "modify", "debug") and \
            domain["unknown"] and not product_type:
        need("domain", "حوزه محصول مشخص نیست و الزامات را تغییر میدهد",
             ["بازی", "آموزش", "کسب و کار", "اتوماسیون", "محتوا"])
    if intent["primary"] in ("create", "modify") and platform["unknown"]:
        need("platform", "پلتفرم تحویل مشخص نیست؛ بازی لزوماً اندروید نیست",
             ["Android", "Web", "Desktop", "Cross-platform"])
    return missing


def clarification_state(text: str, intent: dict, domain: dict, platform: dict,
                        answers: list[dict], product_type: str = "") -> dict:
    missing = essential_missing(text, intent, domain, platform, answers,
                                product_type)
    essential = [m for m in missing if m["essential"]]
    optional = [m for m in missing if not m["essential"]]
    question = ""
    options = []
    if essential:
        m = essential[0]
        question = "برای ادامهٔ ایمن، %s را مشخص کنید. چرا: %s" % (m["id"], m["why"])
        options = m["options"]
    return {"missing": missing, "essential": essential, "optional": optional,
            "question": question, "options": options,
            "can_proceed": not essential}


# ---------------------------------------------------------------------------
# Canonical record construction
# ---------------------------------------------------------------------------

def canonical_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()[:24]


def _absurd(text: str) -> bool:
    return bool(re.search(r"rm\s+-rf|sudo|chmod\s+777|/etc/shadow|drop\s+table|"
                          r"حذف\s*(?:همه|تمام)|دسترسی\s*مطلق", text, re.I))


def new_record(raw_text: str, project: str = "", source_channel: str = "",
               request_id: str = "") -> dict:
    text = str(raw_text or "")
    norm = normalize(text)
    intent = classify_intent(text)
    try:
        from core import requestcheck
        ptype = str(requestcheck.analyze(text).get("primary_type") or "")
    except Exception:  # noqa: BLE001 — optional legacy type hint
        ptype = ""
    domain = classify_domain(text, ptype)
    platform = classify_platform(text)
    risk = classify_risk(text)
    complexity = classify_complexity(text)
    reqs = extract_requirements(text, ptype)
    conflicts = detect_conflicts(reqs, text)
    clar = clarification_state(text, intent, domain, platform, [], ptype)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    rid = str(request_id or ("req-" + hashlib.sha256(
        (text + "|" + project + "|" + str(time.time_ns())).encode()).hexdigest()[:18]))
    risky = risk["level"] in ("high", "critical")
    blocked = _absurd(norm) or risk["level"] == "critical"
    status = "BLOCKED" if blocked else (
        "NEEDS_CLARIFICATION" if not clar["can_proceed"] else "CLASSIFIED")
    rec = {
        "request_id": rid, "project": str(project or ""),
        "schema_version": SCHEMA_VERSION, "created": now, "updated": now,
        "revision": 1, "status": status,
        "history": [{"from": "", "to": status, "at": now,
                     "reason": "intake"}],
        "source_channel": str(source_channel or "api"),
        "raw_text": text[:8000], "normalized_text": norm[:8000],
        "intent": {"primary": intent["primary"], "secondary": intent["secondary"],
                   "outcome": text[:400], "goal": text[:400],
                   "product_type": ptype, "confidence": intent["confidence"],
                   "evidence": intent["evidence"]},
        "requirements": reqs,
        "constraints": {"user": [], "technical": [], "budget": [],
                        "time": [], "connectivity": [], "privacy_security": []},
        "assumptions": [],
        "classification": {
            "domain": domain, "platform": platform, "intent": intent,
            "risk": risk, "complexity": complexity,
            "autonomy_eligible": (risk["level"] == "low" and not blocked),
            "confidence": round(min(domain["confidence"],
                                    platform["confidence"],
                                    intent["confidence"]), 2),
            "reasons": intent["evidence"], "conflicts": conflicts,
        },
        "clarification": {"missing": clar["missing"], "asked": [],
                          "answers": [], "can_proceed": clar["can_proceed"]},
        "plan": {}, "approval": {"required": risky, "status": "NOT_REQUIRED",
                                 "request_revision": "", "request_hash": "",
                                 "plan_hash": "", "scope": "", "actor": "",
                                 "token": "", "created": "", "expires": 0,
                                 "invalidated": False},
        "legacy": {}, "migration_source": "", "migration_version": "",
    }
    rec["request_hash"] = request_hash(rec)
    return rec


def request_hash(rec: dict) -> str:
    return canonical_hash({"request_id": rec.get("request_id", ""),
                           "revision": rec.get("revision", 0),
                           "raw_text": rec.get("raw_text", ""),
                           "requirements": rec.get("requirements", []),
                           "classification": rec.get("classification", {})})


def plan_hash(plan: dict) -> str:
    return canonical_hash({k: v for k, v in (plan or {}).items()
                           if k not in ("hash", "status", "steps_status")})


def validate_record(rec: dict) -> list[str]:
    errors = []
    if not isinstance(rec, dict):
        return ["record-not-dict"]
    if not rec.get("request_id"):
        errors.append("request-id-required")
    if rec.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported-schema")
    if rec.get("status") not in STATES:
        errors.append("invalid-status")
    if not isinstance(rec.get("revision"), int) or rec["revision"] < 1:
        errors.append("invalid-revision")
    if not isinstance(rec.get("requirements"), list):
        errors.append("requirements-not-list")
    else:
        ids = set()
        for r in rec["requirements"]:
            if not isinstance(r, dict) or not r.get("id"):
                errors.append("requirement-id-required")
            elif r["id"] in ids:
                errors.append("duplicate-requirement-id")
            else:
                ids.add(r["id"])
            if r.get("source") not in ("explicit", "inferred", "system"):
                errors.append("invalid-requirement-source")
    cls = rec.get("classification") or {}
    if cls.get("risk", {}).get("level") not in RISK_LEVELS:
        errors.append("invalid-risk-level")
    if cls.get("complexity", {}).get("level") not in COMPLEXITY_LEVELS:
        errors.append("invalid-complexity-level")
    if cls.get("domain", {}).get("value") not in DOMAINS:
        errors.append("invalid-domain")
    if cls.get("platform", {}).get("value") not in PLATFORMS:
        errors.append("invalid-platform")
    return errors


_PLATFORM_ANSWERS = (("Android", r"android|اندروید"),
                     ("Web", r"\bweb\b|وب"),
                     ("Desktop", r"desktop|دسکتاپ|windows"),
                     ("Cross-platform", r"cross|چند\s*پلتفرم|همه\s*پلتفرم"))
_DOMAIN_ANSWERS = (("game", r"game|بازی"),
                   ("education", r"education|آموزش"),
                   ("business", r"business|کسب\s*و\s*کار|فروشگاه"),
                   ("automation", r"automation|اتوماسیون"),
                   ("content", r"content|محتوا"))
_INTENT_ANSWERS = (("create", r"create|بساز|ساخت"),
                   ("modify", r"modify|تغییر|ویرایش"),
                   ("debug", r"debug|fix|رفع\s*(?:مشکل|خطا)"),
                   ("explain", r"explain|توضیح"),
                   ("analyze", r"analy|تحلیل|بررسی"),
                   ("plan", r"plan|برنامه"))


def resolve_answer(question_id: str, answer_text: str) -> dict:
    """Validate an answer and map it to a canonical value when possible.

    Answers are untrusted user data: a non-resolving answer never clears a
    question and never grants permission.
    """
    qid = str(question_id or "")
    raw = str(answer_text or "").strip()
    low = raw.lower()
    if not raw or re.fullmatch(r"(?i)(unknown|نمیدانم|نمیدونم|نامشخص|none|n/?a|-+)", low):
        return {"resolved": False, "value": "", "reason": "empty-or-unknown-answer"}
    if qid == "platform":
        for value, pat in _PLATFORM_ANSWERS:
            if re.search(pat, low):
                return {"resolved": True, "value": value, "reason": "platform-answer"}
        return {"resolved": False, "value": "", "reason": "unrecognized-platform"}
    if qid == "domain":
        for value, pat in _DOMAIN_ANSWERS:
            if re.search(pat, low):
                return {"resolved": True, "value": value, "reason": "domain-answer"}
        return {"resolved": False, "value": "", "reason": "unrecognized-domain"}
    if qid == "intent":
        for value, pat in _INTENT_ANSWERS:
            if re.search(pat, low):
                return {"resolved": True, "value": value, "reason": "intent-answer"}
        return {"resolved": False, "value": "", "reason": "unrecognized-intent"}
    return {"resolved": True, "value": raw[:400], "reason": "free-text-answer"}


def apply_answers(rec: dict, answers: list[dict]) -> dict:
    """Recompute every dependent field from the original text plus answers."""
    original = str(rec.get("normalized_text") or "")
    pieces = [original] + [str(a.get("value") or a.get("answer") or "")
                           for a in answers if a.get("resolved")]
    effective = normalize(" ".join(pieces))
    ptype = str((rec.get("intent") or {}).get("product_type") or "")

    intent = classify_intent(effective)
    domain = classify_domain(effective, ptype)
    platform = classify_platform(effective)
    for a in answers:
        if not a.get("resolved"):
            continue
        qid, value = a.get("question_id"), a.get("value")
        if qid == "platform" and value:
            platform = {"value": value, "platforms": [value], "confidence": 0.95,
                        "evidence": ["clarification-answer:platform"], "unknown": False}
        elif qid == "domain" and value:
            domain = {"value": value, "confidence": 0.95,
                      "evidence": ["clarification-answer:domain"], "unknown": False}
        elif qid == "intent" and value:
            intent = {"primary": value, "secondary": intent.get("secondary", ""),
                      "confidence": 0.95, "evidence": ["clarification-answer:intent"],
                      "uncertain": False}

    cls = rec.setdefault("classification", {})
    cls["intent"], cls["domain"], cls["platform"] = intent, domain, platform
    top_intent = rec.setdefault("intent", {})
    top_intent["primary"] = intent["primary"]
    top_intent["secondary"] = intent.get("secondary", "")
    top_intent["confidence"] = intent["confidence"]
    top_intent["evidence"] = intent["evidence"]
    cls["risk"] = classify_risk(effective)
    cls["complexity"] = classify_complexity(effective)
    cls["autonomy_eligible"] = (cls["risk"]["level"] == "low" and
                                rec.get("status") != "BLOCKED")
    cls["confidence"] = round(min(domain["confidence"], platform["confidence"],
                                  intent["confidence"]), 2)
    cls["reasons"] = intent["evidence"]

    # Preserve the user's original explicit requirements; refresh the rest.
    old_explicit = [dict(r) for r in rec.get("requirements", [])
                    if r.get("source") == "explicit"]
    fresh = extract_requirements(effective, ptype)
    refreshed = old_explicit + [dict(r) for r in fresh
                                if r.get("source") != "explicit"]
    for idx, req in enumerate(refreshed, 1):
        req["id"] = "req-%02d" % idx
    rec["requirements"] = refreshed
    cls["conflicts"] = detect_conflicts(refreshed, effective)

    rec["constraints"] = rec.get("constraints") or {}
    rec["assumptions"] = [a for a in rec.get("assumptions", [])
                          if a.get("source") != "clarification"]
    for a in answers:
        if a.get("resolved") and a.get("question_id") in ("domain", "platform", "intent"):
            rec["assumptions"].append({
                "description": "پاسخ کاربر: %s = %s" % (a["question_id"], a["value"]),
                "reason": a.get("reason", "clarification-answer"), "confidence": 0.95,
                "source": "clarification", "revision": a.get("revision")})

    state = clarification_state(effective, intent, domain, platform,
                                [a for a in answers if a.get("resolved")], ptype)
    clar = rec.setdefault("clarification", {})
    for ans in answers:
        qid = ans.get("question_id")
        if not ans.get("resolved") and qid in ("intent", "domain", "platform"):
            if qid not in [m["id"] for m in state["missing"]]:
                state["missing"].append({"id": qid, "essential": True,
                                          "why": "پاسخ نامعتبر بود؛ اطلاعات ضروری هنوز نامشخص است",
                                          "options": []})
    state["essential"] = [m for m in state["missing"] if m["essential"]]
    state["can_proceed"] = not state["essential"]
    clar["missing"] = state["missing"]
    clar["can_proceed"] = state["can_proceed"]
    rec["normalized_text"] = effective[:8000]
    return state


def _route_after_answer(rec: dict, can_proceed: bool, reason: str) -> None:
    """Move back through legal transitions; answers never grant execution."""
    if rec.get("status") in ("REJECTED", "BLOCKED"):
        return
    if rec["status"] == "RECEIVED":
        _transition(rec, "CLASSIFIED", reason)
    if can_proceed:
        if rec["status"] == "NEEDS_CLARIFICATION":
            _transition(rec, "CLASSIFIED", reason)
        if rec["status"] in ("CLASSIFIED", "PLANNED", "AWAITING_APPROVAL"):
            _transition(rec, "READY_FOR_PLAN", reason)
    else:
        if rec["status"] in ("PLANNED", "AWAITING_APPROVAL"):
            _transition(rec, "READY_FOR_PLAN", reason)
        if rec["status"] in ("CLASSIFIED", "READY_FOR_PLAN"):
            _transition(rec, "NEEDS_CLARIFICATION", reason)


def _execution_bindings(rec: dict, project: str, goal: str,
                        plan_steps: list) -> dict:
    """Canonical bindings that every execution capability must match."""
    return {"request_id": rec["request_id"], "revision": rec["revision"],
            "request_hash": rec["request_hash"],
            "plan_hash": (rec.get("plan") or {}).get("hash", ""),
            "steps_hash": canonical_hash({"steps": plan_steps}),
            "project": str(project),
            "goal_hash": canonical_hash({"goal": str(goal)}),
            "scope": "execute-bounded-plan"}


def _validate_execution(rec: dict, project: str, goal: str,
                        plan: dict | None, scope: str, actor: str,
                        session_id: str) -> dict:
    """Read-only validation shared by can_execute and atomic consumption."""
    status = rec.get("status")
    if status in ("BLOCKED", "REJECTED", "NEEDS_CLARIFICATION", "AWAITING_APPROVAL"):
        return {"allowed": False, "reason": "status:" + status, "status": status}
    ex = rec.get("execution") or {}
    if ex.get("state") != "AUTHORIZED" or ex.get("invalidated"):
        return {"allowed": False, "reason": "no-execution-authorization",
                "status": status}
    bindings = _execution_bindings(rec, project, goal,
                                   (plan or {}).get("steps") or [])
    for key in ("request_hash", "revision", "plan_hash"):
        if ex.get(key) != bindings[key]:
            return {"allowed": False, "reason": "execution-authorization-stale",
                    "status": status}
    if ex.get("project") != bindings["project"]:
        return {"allowed": False, "reason": "execution-project-mismatch",
                "status": status}
    if ex.get("goal_hash") != bindings["goal_hash"]:
        return {"allowed": False, "reason": "execution-goal-mismatch",
                "status": status}
    if ex.get("scope") != scope:
        return {"allowed": False, "reason": "execution-scope-mismatch",
                "status": status}
    if plan is None or not isinstance(plan.get("steps"), list):
        return {"allowed": False, "reason": "bounded-plan-required",
                "status": status}
    if ex.get("steps_hash") != canonical_hash({"steps": plan["steps"]}):
        return {"allowed": False, "reason": "execution-steps-mismatch",
                "status": status}
    if actor and ex.get("actor") != str(actor):
        return {"allowed": False, "reason": "execution-actor-mismatch",
                "status": status}
    if session_id:
        from core import panelauth
        if not panelauth.session_token_valid(session_id):
            return {"allowed": False, "reason": "execution-session-invalid",
                    "status": status}
        if ex.get("session_id") and ex["session_id"] != session_id:
            return {"allowed": False, "reason": "execution-session-mismatch",
                    "status": status}
    if ex.get("expires") and float(ex["expires"]) < time.time():
        return {"allowed": False, "reason": "execution-authorization-expired",
                "status": status}
    risk = (rec.get("classification") or {}).get("risk", {}).get("level")
    ap = rec.get("approval") or {}
    if risk in ("high", "critical"):
        if ap.get("status") != "APPROVED" or ap.get("invalidated") or \
                not ap.get("actor"):
            return {"allowed": False, "reason": "explicit-approval-required",
                    "status": status}
        if ap.get("request_hash") != rec.get("request_hash") or \
                ap.get("plan_hash") != (rec.get("plan") or {}).get("hash"):
            return {"allowed": False, "reason": "approval-bindings-stale",
                    "status": status}
    return {"allowed": True, "reason": "explicit-execution-authorization",
            "status": status, "scope": scope, "actor": ex.get("actor"),
            "request_id": rec["request_id"]}


def propose_execution(request_id: str, actor: str, scope: str,
                      project: str, goal: str, plan_steps: list,
                      session_id: str = "") -> dict:
    """Step 1 of 3 — propose an execution capability.

    Creation is NOT authorization. A confirmation token is issued and the
    canonical record is put into ``PROPOSED``. Nothing may execute yet.
    """
    actor = str(actor or "").strip()
    if not actor:
        raise RequestError("execution-actor-required")
    if scope != "execute-bounded-plan":
        raise RequestError("execution-scope-unsupported:" + scope)
    from core import confirmations

    with _request_lock():
        items = _load_unlocked()
        rec = items.get(str(request_id or ""))
        if rec is None:
            raise RequestError("request-not-found:" + request_id)
        if rec.get("status") in ("BLOCKED", "REJECTED", "NEEDS_CLARIFICATION"):
            raise RequestError("execution-not-ready:" + rec["status"])
        plan = rec.get("plan") or {}
        if not plan.get("hash") or not isinstance(plan.get("steps"), list):
            raise RequestError("bounded-plan-required")
        risk = (rec.get("classification") or {}).get("risk", {}).get("level")
        ap = rec.get("approval") or {}
        if risk in ("high", "critical") and \
                not (ap.get("status") == "APPROVED" and not ap.get("invalidated")):
            raise RequestError("explicit-approval-required-for-execution")
        bindings = _execution_bindings(rec, project, goal, plan["steps"])
        cf = confirmations.create(str(project), "phase02-execution-authorization",
                                  bindings, "تأیید صریح اجرای محدود لازم است",
                                  session_id=session_id)
        rec["execution"] = {"state": "PROPOSED", "authorized": False,
                            "actor": actor, "actor_trusted": False,
                            "scope": scope, "bindings": bindings,
                            "token": cf["confirm_token"],
                            "session_id": str(session_id or ""),
                            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "invalidated": False}
        items[rec["request_id"]] = rec
        _save_unlocked(items)
        return {"request_id": rec["request_id"],
                "confirm_token": cf["confirm_token"],
                "expires_in_s": cf.get("expires_in_s", 0),
                "question": cf["question"]}


def confirm_execution(request_id: str, token: str, actor: str, scope: str,
                      project: str, goal: str, plan_steps: list,
                      session_id: str = "") -> dict:
    """Step 2 of 3 — genuine explicit confirmation issues the capability.

    Possession of the externally presented one-shot confirmation token is the
    explicit user action. A caller-supplied actor string alone never suffices.
    """
    actor = str(actor or "").strip()
    if not actor:
        raise RequestError("execution-actor-required")
    if scope != "execute-bounded-plan":
        raise RequestError("execution-scope-unsupported:" + scope)
    from core import confirmations

    with _request_lock():
        items = _load_unlocked()
        rec = items.get(str(request_id or ""))
        if rec is None:
            raise RequestError("request-not-found:" + request_id)
        ex = rec.get("execution") or {}
        if ex.get("state") != "PROPOSED":
            raise RequestError("execution-not-proposed:" + str(ex.get("state")))
        if not token or ex.get("token") != str(token):
            raise RequestError("execution-token-mismatch")
        try:
            v = confirmations.peek(str(token), str(project),
                                   session_id=session_id)
        except confirmations.ConfirmError as exc:
            raise RequestError("execution-confirmation-invalid:" + exc.message) \
                from exc
        payload = v.get("payload") or {}
        expected = _execution_bindings(rec, project, goal,
                                       (rec.get("plan") or {}).get("steps") or [])
        for key, value in expected.items():
            if payload.get(key) != value:
                raise RequestError("execution-confirmation-binding:" + key)
        trusted = False
        if session_id:
            from core import panelauth
            trusted = bool(panelauth.session_token_valid(session_id))
            if not trusted:
                raise RequestError("execution-session-invalid")
        # Presenting a genuine one-shot confirmation token is the explicit
        # user action; an authenticated panel session also establishes trust.
        trusted = True
        confirmations.consume(str(token), str(project), session_id=session_id)
        ex.update({"state": "AUTHORIZED", "authorized": True,
                   "actor": actor, "actor_trusted": trusted,
                   "scope": scope, "project": str(project),
                   "goal_hash": expected["goal_hash"],
                   "revision": expected["revision"],
                   "request_hash": expected["request_hash"],
                   "plan_hash": expected["plan_hash"],
                   "steps_hash": expected["steps_hash"],
                   "session_id": str(session_id or ""),
                   "authorized_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "expires": float(v.get("expires") or 0),
                   "consumed_at": None})
        items[rec["request_id"]] = rec
        _save_unlocked(items)
        return dict(ex)


def authorize_execution(request_id: str, actor: str, scope: str,
                        project: str, goal: str, plan_steps: list,
                        session_id: str = "", token: str = "") -> dict:
    """Compatibility entrypoint: requires an explicit confirmation token.

    Without ``token`` it fails closed; there is deliberately no path that
    issues a capability from a caller-supplied actor string alone.
    """
    if not str(token or ""):
        raise RequestError("explicit-confirmation-required")
    return confirm_execution(request_id, token, actor, scope, project, goal,
                             plan_steps, session_id=session_id)


def can_execute(request_id: str, project: str = "", goal: str = "",
                plan: dict | None = None,
                scope: str = "execute-bounded-plan", actor: str = "",
                session_id: str = "") -> dict:
    """READ-ONLY validation. Never consumes a capability; never authorizes."""
    rec = get(request_id)
    if rec is None:
        return {"allowed": False, "reason": "request-not-found",
                "request_id": request_id}
    result = _validate_execution(rec, project, goal, plan, scope, actor,
                                 session_id)
    result["request_id"] = request_id
    return result


def consume_execution(request_id: str, project: str, goal: str,
                      plan: dict | None,
                      scope: str = "execute-bounded-plan", actor: str = "",
                      session_id: str = "") -> dict:
    """Step 3 of 3 — atomically consume the capability at the execution edge.

    The AUTHORIZED -> CONSUMED transition happens under the canonical request
    lock BEFORE any external, model, tool or pipeline side effect. A replay,
    concurrent attempt, stale plan or storage failure fails closed. If the
    execution later fails, the capability is NOT restored.
    """
    with _request_lock():
        try:
            items = _load_unlocked()
        except RequestStoreError as exc:
            return {"allowed": False, "reason": "canonical-store-unavailable:" +
                    str(exc)[:80], "request_id": request_id}
        rec = items.get(str(request_id or ""))
        if rec is None:
            return {"allowed": False, "reason": "request-not-found",
                    "request_id": request_id}
        result = _validate_execution(rec, project, goal, plan, scope, actor,
                                     session_id)
        if not result["allowed"]:
            result["request_id"] = request_id
            return result
        ex = rec["execution"]
        ex["state"] = "CONSUMED"
        ex["authorized"] = False
        ex["consumed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        ex["consumed_by"] = str(actor or ex.get("actor") or "")
        items[rec["request_id"]] = rec
        try:
            _save_unlocked(items)
        except RequestStoreError as exc:
            return {"allowed": False, "reason": "execution-persistence-failed:" +
                    str(exc)[:80], "request_id": request_id}
        result.update({"allowed": True, "reason": "capability-consumed",
                       "consumed_at": ex["consumed_at"]})
        return result


def _transition(rec: dict, to: str, reason: str) -> None:
    if to == rec["status"]:
        return
    current = rec["status"]
    if to not in TRANSITIONS.get(current, ()):
        raise RequestError("invalid-transition:%s->%s" % (current, to))
    rec["history"].append({"from": current, "to": to,
                           "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "reason": str(reason)[:200]})
    rec["status"] = to
    rec["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")


def _revise(rec: dict, reason: str) -> None:
    rec["revision"] = int(rec.get("revision") or 1) + 1
    rec["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    rec.setdefault("history", []).append({
        "from": rec.get("status", ""), "to": rec.get("status", ""),
        "at": rec["updated"], "reason": "revision:" + str(reason)[:120]})
    ap = rec.get("approval") or {}
    if ap.get("token") and not ap.get("invalidated"):
        ap["invalidated"] = True
    rec["request_hash"] = request_hash(rec)


# ---------------------------------------------------------------------------
# Public lifecycle API
# ---------------------------------------------------------------------------

def intake(raw_text: str, project: str = "", source_channel: str = "",
           request_id: str = "") -> dict:
    if not str(raw_text or "").strip():
        raise RequestError("empty-request")
    rec = new_record(raw_text, project, source_channel, request_id)
    errors = validate_record(rec)
    if errors:
        raise RequestStoreError("invalid-generated-record:" + ",".join(errors))
    with _request_lock():
        items = _load_unlocked()
        rid = rec["request_id"]
        if rid in items:
            # Duplicate submission: never create a second canonical record.
            return dict(items[rid])
        items[rid] = rec
        _save_unlocked(items)
    return rec


def get(request_id: str) -> dict | None:
    with _request_lock():
        value = _load_unlocked().get(str(request_id or ""))
        return dict(value) if isinstance(value, dict) else None


def list_requests(project: str = "") -> list[dict]:
    with _request_lock():
        items = _load_unlocked()
    out = list(items.values())
    if project:
        out = [r for r in out if r.get("project") == project]
    return sorted(out, key=lambda r: r.get("created", ""))


def save_view(rec: dict) -> dict:
    """API-safe projection; never exposes approval tokens or raw secrets."""
    return {"request_id": rec.get("request_id"), "project": rec.get("project"),
            "schema_version": rec.get("schema_version"),
            "revision": rec.get("revision"), "status": rec.get("status"),
            "raw_text": rec.get("raw_text"),
            "intent": rec.get("intent"), "requirements": rec.get("requirements"),
            "classification": rec.get("classification"),
            "clarification": {"missing": rec.get("clarification", {}).get("missing"),
                              "can_proceed": rec.get("clarification", {}).get("can_proceed"),
                              "asked": rec.get("clarification", {}).get("asked")},
            "plan": rec.get("plan"),
            "approval": {k: v for k, v in (rec.get("approval") or {}).items()
                         if k not in ("token",)},
            "request_hash": rec.get("request_hash"),
            "history": rec.get("history")}


def answer(request_id: str, answer_text: str, question_id: str = "") -> dict:
    """Apply a clarification answer to the canonical request, traceably.

    The answer is stored separately (never merged into ``raw_text``), validated,
    mapped to a canonical value, and used to recompute every dependent field.
    An unresolved answer never clears a question and never grants permission.
    """
    raw = str(answer_text or "").strip()
    if not raw:
        raise RequestError("empty-answer")

    def fn(rec):
        if rec is None:
            return None
        if rec.get("status") in ("REJECTED", "BLOCKED"):
            raise RequestError("request-terminal:" + rec["status"])
        clar = rec.setdefault("clarification", {})
        qid = str(question_id or (clar.get("missing") or [{}])[0].get("id", "general"))
        if any(a.get("question_id") == qid and a.get("resolved")
               for a in clar.get("answers", [])):
            raise RequestError("question-already-resolved:" + qid)
        resolved = resolve_answer(qid, raw)
        new_rev = int(rec.get("revision") or 1) + 1
        entry = {"question_id": qid, "answer": raw[:2000],
                 "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "revision": new_rev, "provenance": "user-clarification",
                 "resolved": bool(resolved["resolved"]),
                 "value": resolved["value"], "reason": resolved["reason"]}
        clar.setdefault("answers", []).append(entry)

        state = apply_answers(rec, clar["answers"])
        if resolved["resolved"]:
            plan = rec.get("plan") or {}
            if plan:
                plan["invalidated"], plan["stale"] = True, True
            ap = rec.setdefault("approval", {})
            if ap.get("status") in ("APPROVED", "PENDING"):
                ap["status"] = "INVALIDATED"
            ap["invalidated"] = True
            ap["token"] = ""
        _route_after_answer(rec, state["can_proceed"],
                            "answer-" + qid + ("-resolved" if resolved["resolved"]
                                               else "-unresolved"))

        # Finalise the revision AFTER every mutation so the hash covers it all.
        rec["revision"] = new_rev
        rec["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        rec.setdefault("history", []).append({
            "from": rec.get("status", ""), "to": rec.get("status", ""),
            "at": rec["updated"],
            "reason": "revision:answer:" + qid + ":" + resolved["reason"]})
        rec["request_hash"] = request_hash(rec)
        return rec

    return _mutate(request_id, fn)


def build_plan(request_id: str) -> dict:
    """Generate a bounded, reviewable plan. Never executes any step."""

    def fn(rec):
        if rec is None:
            return None
        if rec.get("status") in ("REJECTED", "BLOCKED"):
            raise RequestError("request-not-plannable:" + rec["status"])
        if not rec.get("clarification", {}).get("can_proceed", False):
            raise RequestError("essential-clarification-required")
        complexity = rec["classification"]["complexity"]["level"]
        platform = rec["classification"]["platform"]["value"]
        domain = rec["classification"]["domain"]["value"]
        steps = [{
            "id": "inspect", "purpose": "Collect real project facts",
            "required_input": ["request record", "project snapshot"],
            "expected_output": "verified fact list", "depends_on": [],
            "acceptance_condition": "facts recorded and traceable",
            "risk": "low", "approval_required": False, "status": "QUEUED"},
            {"id": "specify", "purpose": "Turn requirements into a bounded spec",
             "required_input": ["explicit requirements", "constraints"],
             "expected_output": "reviewable specification", "depends_on": ["inspect"],
             "acceptance_condition": "every explicit requirement is represented",
             "risk": "low", "approval_required": False, "status": "QUEUED"},
            {"id": "verify", "purpose": "Verify the later implementation",
             "required_input": ["acceptance conditions"],
             "expected_output": "verification evidence", "depends_on": ["specify"],
             "acceptance_condition": "acceptance conditions checked with real evidence",
             "risk": "low", "approval_required": False, "status": "QUEUED"},
            {"id": "deliver", "purpose": "High-level delivery handoff (not executed here)",
             "required_input": ["verification evidence"],
             "expected_output": "handoff description", "depends_on": ["verify"],
             "acceptance_condition": "delivery scope matches approval",
             "risk": "low", "approval_required": False, "status": "QUEUED"},
        ]
        if platform == "Android":
            steps.insert(3, {"id": "package",
                             "purpose": "Describe Android packaging step",
                             "required_input": ["specification"],
                             "expected_output": "packaging plan",
                             "depends_on": ["specify"],
                             "acceptance_condition": "platform requirement preserved",
                             "risk": "low", "approval_required": False,
                             "status": "QUEUED"})
        if domain == "game":
            steps.insert(3, {"id": "domain-review",
                             "purpose": "Describe later domain review (ADR-011 unresolved)",
                             "required_input": ["specification"],
                             "expected_output": "deferred decision note",
                             "depends_on": ["specify"],
                             "acceptance_condition": "no game-engine decision assumed",
                             "risk": "low", "approval_required": False,
                             "status": "QUEUED"})
        if complexity in ("large", "very-large"):
            steps.insert(1, {"id": "decompose",
                             "purpose": "Decompose work into bounded parts",
                             "required_input": ["request record"],
                             "expected_output": "component list",
                             "depends_on": ["inspect"],
                             "acceptance_condition": "dependencies identified",
                             "risk": "low", "approval_required": False,
                             "status": "QUEUED"})
        seen, unique = set(), []
        for s in steps:
            if s["id"] in seen:
                continue
            seen.add(s["id"])
            unique.append(s)
        plan = {"id": "plan-" + rec["request_id"], "revision": 1,
                "goal": rec.get("intent", {}).get("goal", "")[:400],
                "steps": unique,
                "expected_outputs": [s["expected_output"] for s in unique],
                "acceptance_conditions": [s["acceptance_condition"] for s in unique],
                "risks": rec["classification"]["risk"]["reasons"],
                "required_approvals": [],
                "dependencies": [s["depends_on"] for s in unique],
                "estimates": {"time": "unknown", "cost": "unknown"},
                "executed": False}
        plan["hash"] = plan_hash(plan)
        rec["plan"] = plan
        _revise(rec, "plan-generated")
        if rec["status"] in ("CLASSIFIED", "NEEDS_CLARIFICATION",
                             "AWAITING_APPROVAL"):
            _transition(rec, "READY_FOR_PLAN", "plan-ready")
        _transition(rec, "PLANNED", "plan-generated")
        risk = rec["classification"]["risk"]["level"]
        if (risk in ("high", "critical") or rec["approval"]["required"] or
                not rec["classification"]["autonomy_eligible"]):
            _transition(rec, "AWAITING_APPROVAL", "approval-required")
        else:
            # Planning is NEVER approval. A low-risk plan simply does not
            # require a human approval in this phase; no system actor may
            # impersonate explicit user consent.
            ap = rec["approval"]
            ap.update({"status": "NOT_REQUIRED", "actor": "", "actor_trusted": False,
                       "request_revision": rec["revision"],
                       "request_hash": rec["request_hash"],
                       "plan_hash": plan["hash"], "scope": "",
                       "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "invalidated": False})
        return rec

    return _mutate(request_id, fn)


def approval_proposal(request_id: str, session_id: str = "") -> dict:
    """Create a one-shot approval token bound to the exact current record.

    The whole read/create/bind/save sequence holds the cross-process lock so a
    concurrent mutation cannot bind a token to a stale revision.
    """
    from core import confirmations
    with _request_lock():
        items = _load_unlocked()
        rec = items.get(str(request_id or ""))
        if rec is None:
            raise RequestError("request-not-found:" + request_id)
        if rec["status"] != "AWAITING_APPROVAL":
            raise RequestError("approval-not-required:" + rec["status"])
        project = rec.get("project", "")
        payload = {"request_id": rec["request_id"], "revision": rec["revision"],
                   "request_hash": rec["request_hash"],
                   "plan_hash": (rec.get("plan") or {}).get("hash", ""),
                   "scope": "execute-bounded-plan"}
        cf = confirmations.create(project, "phase02-plan-approval", payload,
                                  "تأیید صریح مورد نیاز است: " + rec["request_id"][:40],
                                  session_id=session_id)
        # Authoritative absolute expiry comes from the confirmation store.
        v = confirmations.peek(cf["confirm_token"], project, session_id=session_id)
        a = rec["approval"]
        a.update({"token": cf["confirm_token"], "status": "PENDING",
                  "request_revision": rec["revision"],
                  "request_hash": rec["request_hash"],
                  "plan_hash": (rec.get("plan") or {}).get("hash", ""),
                  "scope": "execute-bounded-plan",
                  "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                  "expires": float(v.get("expires") or 0),
                  "expires_in_s": cf.get("expires_in_s", 0),
                  "invalidated": False})
        items[rec["request_id"]] = rec
        _save_unlocked(items)
        return {"request_id": rec["request_id"],
                "confirm_token": cf["confirm_token"], "question": cf["question"],
                "expires_in_s": cf["expires_in_s"],
                "request_hash": rec["request_hash"],
                "plan_hash": (rec.get("plan") or {}).get("hash", "")}


def approve(request_id: str, token: str, actor: str,
            project: str = "", session_id: str = "") -> dict:
    """Validate and consume a bound one-shot approval; fail closed on mismatch.

    Validation, authoritative expiry checking, token consumption and the
    terminal mutation are performed inside ONE cross-process critical section.
    A caller-supplied actor string is recorded but is NOT claimed as an
    authenticated identity unless a trusted panel session is also supplied.
    """
    actor = str(actor or "").strip()
    if not actor:
        raise RequestError("approval-actor-required")
    token = str(token or "")
    from core import confirmations

    with _request_lock():
        items = _load_unlocked()
        rec = items.get(str(request_id or ""))
        if rec is None:
            raise RequestError("request-not-found:" + request_id)
        ap = rec.get("approval") or {}
        if ap.get("invalidated"):
            raise RequestError("approval-invalidated")
        if rec["status"] != "AWAITING_APPROVAL":
            raise RequestError("approval-not-pending:" + rec["status"])
        if not ap.get("token") or ap["token"] != token:
            raise RequestError("approval-token-mismatch")
        use_project = project or rec.get("project", "")
        try:
            v = confirmations.peek(token, use_project, session_id=session_id)
        except confirmations.ConfirmError as exc:
            raise RequestError("approval-not-valid:" + exc.message) from exc
        payload = v.get("payload") or {}
        expected = {"request_id": rec["request_id"], "revision": rec["revision"],
                    "request_hash": rec["request_hash"],
                    "plan_hash": (rec.get("plan") or {}).get("hash", ""),
                    "scope": "execute-bounded-plan"}
        for key, value in expected.items():
            if payload.get(key) != value:
                raise RequestError("approval-binding-mismatch:" + key)
        if float(v.get("expires") or 0) < time.time():
            raise RequestError("approval-expired")

        trusted = False
        if session_id:
            from core import panelauth
            trusted = bool(panelauth.session_token_valid(session_id))
            if not trusted:
                raise RequestError("approval-session-invalid")
        # Consume only after every binding check passes; replay then fails closed.
        confirmations.consume(token, use_project, session_id=session_id)
        ap.update({"status": "APPROVED", "actor": actor,
                   "actor_trusted": trusted,
                   "approved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "token": "", "invalidated": False})
        _transition(rec, "APPROVED", "explicit-approval")
        items[rec["request_id"]] = rec
        _save_unlocked(items)
        return rec


def reject(request_id: str, actor: str, reason: str = "") -> dict:
    if not str(actor or "").strip():
        raise RequestError("reject-actor-required")

    def fn(rec):
        if rec is None:
            return None
        _transition(rec, "REJECTED", str(reason or "user-rejected")[:160])
        rec["approval"]["status"] = "REJECTED"
        return rec

    return _mutate(request_id, fn)


# ---------------------------------------------------------------------------
# Legacy migration and controlled compatibility
# ---------------------------------------------------------------------------

def compat_mode() -> str:
    try:
        d = json.loads(_FLAGS.read_text(encoding="utf-8"))
        return "legacy" if d.get("mode") == "legacy" else "canonical"
    except (OSError, ValueError):
        return "canonical"


def set_compat_mode(mode: str) -> dict:
    if mode not in ("canonical", "legacy"):
        raise RequestError("invalid-compat-mode")
    _FLAGS.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(_FLAGS, json.dumps({"mode": mode}, ensure_ascii=False))
    return {"mode": mode}


def migrate_legacy(project: str, legacy: dict) -> dict:
    """Idempotent, non-destructive migration of legacy request fields."""
    legacy = dict(legacy or {})
    goal = str(legacy.get("goal") or legacy.get("text") or "")
    if not goal.strip():
        raise RequestError("legacy-goal-required")
    ref = str(legacy.get("request_id") or legacy.get("task_id") or
               canonical_hash({"goal": goal, "project": project}))
    with _request_lock():
        items = _load_unlocked()
        existing = next((r for r in items.values()
                         if (r.get("legacy") or {}).get("legacy_ref") == ref), None)
        if existing:
            return dict(existing)  # already migrated — never duplicate
        rec = new_record(goal, project, source_channel="legacy-migration")
        rec["legacy"] = {"legacy_ref": ref,
                         "preserved": {k: v for k, v in legacy.items()
                                       if k not in ("goal", "text")},
                         "migrated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        rec["migration_source"] = str(legacy.get("source") or "legacy-request")
        rec["migration_version"] = STORE_VERSION
        rc = legacy.get("request_check") or {}
        if isinstance(rc, dict):
            rec["classification"]["legacy_evidence"] = {
                "primary_type": rc.get("primary_type", ""),
                "missing": rc.get("missing", []),
                "dangerous": rc.get("dangerous", [])}
            if rc.get("allowed") is False:
                _transition(rec, "BLOCKED", "legacy-dangerous-request")
        decision = legacy.get("decision") or {}
        if isinstance(decision, dict) and decision.get("decision") == "CONFIRM":
            rec["approval"]["required"] = True
        ask = legacy.get("ask") or {}
        if isinstance(ask, dict) and ask.get("question"):
            rec["clarification"]["asked"].append({
                "question": str(ask["question"])[:500],
                "options": ask.get("options", []), "source": "legacy"})
        if legacy.get("ask_answer"):
            rec["clarification"]["answers"].append({
                "question_id": "legacy", "answer": str(legacy["ask_answer"])[:500],
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": "legacy"})
        rec["request_hash"] = request_hash(rec)
        items[rec["request_id"]] = rec
        _save_unlocked(items)
        return rec
