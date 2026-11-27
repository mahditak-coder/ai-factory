"""P6-3 — real validation of Persian (and mixed) feature requests BEFORE
planning/generation runs.

Honest, local, offline checks — no model involved:

* intent detection    → the project type the request most likely means
                        (weighted Persian keyword matching, normalized).
* missing essentials  → per-type statements the request must contain
                        (e.g. an api request without any endpoint mention).
* ambiguity           → too short / no recognisable purpose → the plan
                        must not silently invent a scope.
* malicious/dangerous → shell runners, secret harvesting, destructive
                        filesystem/system patterns are REJECTED before they
                        ever reach the model (hard refuse, reason included).

The result is a plain dict the server can attach to planning/generation:
  {ok, allowed, dangerous:[...], ambiguous:bool, intents:[{type,score}],
   primary_type:str, missing:[...], warnings:[...], note:str}
Everything is Persian/RTL-friendly and never mutates the request itself.
"""

import re
from collections import Counter

# Persian/YEN normalisation: unify arabic/persian pairs + digits.
_NORM = {
    "ك": "ک", "ي": "ی", "ؤ": "و", "إ": "ا", "أ": "ا", "ة": "ه",
    "0": "۰", "1": "۱", "2": "۲", "3": "۳", "4": "۴",
    "5": "۵", "6": "۶", "7": "۷", "8": "۸", "9": "۹",
}

# (type, [keywords]) — real, common Persian software-request vocabulary.
_INTENTS = {
    "shop": ("فروشگاه", "سبد", "خرید", "محصول", "پرداخت", "درگاه",
             "چک‌اوت", "تومان"),
    "api": ("api", "بک‌اند", "بکاند", "سرویس", "رست", "rest", "endpoint",
            "پایانه", "webhook", "json"),
    "dashboard": ("داشبورد", "پنل کاربری", "پنل مدیریتی", "گزارش",
                  "نمودار", "chart", "متریک"),
    "webtool": ("ابزار", "مبدل", "محاسبه", "calculator", "کنورتر",
                "formatter", "رمزساز"),
    "ai-tool": ("هوش مصنوعی", "چت‌بات", "چت بات", "llm", "گفتگو",
                "دستیار هوشمند", "خلاصه‌سازی"),
    "fullstack": ("فول‌استک", "fullstack", "وب‌اپلیکیشن", "وب اپلیکیشن",
                  "وب‌اپ", "برنامه تحت وب", "اپلیکیشن وب"),
    "webpage": ("صفحه", "سایت", "وب‌سایت", "وب سایت", "لندینگ", "رزومه",
                "معرفی", "html"),
}

# Essential themes per type: (regex, label) — missing ones are REPORTED.
_ESSENTIALS = {
    "shop": ((r"سبد|خرید|پرداخت|درگاه", "سبد خرید/پرداخت"),
             (r"محصول|کالا", "محصولات"),
             (r"\d+\s*(محصول|کالا|تا)|دسته|دسته‌بندی", "تعداد/دسته محصولات")),
    "api": ((r"endpoint|مسیر|api/|مسیرها", "مسیرها"),
            (r"ذخیره|db|دیتابیس|sqlite|json|فایل", "ذخیره‌سازی")),
    "dashboard": ((r"نمودار|چارت|جدول|گزارش", "نوع نمایش داده"),
                  (r"دیتا|داده|منبع|api", "منبع داده")),
    "webtool": ((r"ورودی|خروجی|فرم|محاسبه|تبدیل", "ورودی/خروجی ابزار"),),
    "ai-tool": ((r"مدل|provider|پروایدر|api-key|کلید", "تأمین مدل/کلید"),
                (r"چت|گفتگو|گپ|سوال|پاسخ", "نوع تعامل")),
    "fullstack": ((r"ثبت‌نام|ورود|لاگین|auth|کاربر", "احراز هویت"),
                  (r"db|دیتابیس|sqlite", "ذخیره‌سازی"),
                  (r"آزمون|test", "آزمون‌ها")),
    "webpage": ((r"تماس|فهرست|بخش|منو|فرم|لینک", "بخش‌های صفحه"),),
}

# Dangerous/malicious request patterns — hard-reject BEFORE the model.
_DANGEROUS = [
    (r"\brm\s+-rf\b", "فرمان تخریبی (rm -rf)"),
    (r"sudo|chmod\s+777|/etc/shadow|/etc/passwd", "دست‌کاری سیستم/اختیارات"),
    (r"curl\b.*\|\s*(ba)?sh|wget\b.*\|\s*(ba)?sh", "اجراى کد از شبکه"),
    (r"\.env\b|openai\s*api\s*key|توکن[ی]? *خودت|call\w*\s*home",
     "آوردن/فاش‌سازی نشانه یا کلید"),
    (r"eval\s*\(|exec\s*\(", "اجرای خودکار کد (eval/exec)"),
    (r"دسترسی مطلق|بالارفتن از|بدون محدودیت صلاحیت|باز کردن تمام فایل",
     "درخواست گریز از محدودیت"),
    (r"حذف\s*کن\s*(همه|تمام|entire)|پاک\s*کن\s*(همه|تمام)|wipe|format\s+c",
     "عملیات تخریبی"),
    (r"<script>alert|onerror=|document\.cookie", "مثال پرتهجوم XSS"),
]


def _normalize(text: str) -> str:
    for k, v in _NORM.items():
        text = text.replace(k, v)
    return text


def _intents(norm: str) -> list[dict]:
    norm_l = norm.lower()
    scores = Counter()
    for itype, kws in _INTENTS.items():
        for kw in kws:
            if kw.lower() in norm_l:
                scores[itype] += 1
    return [{"type": t, "score": s} for t, s in scores.most_common(3)]


def analyze(request: str) -> dict:
    """Full pre-generation analysis of a Persian request (read-only)."""
    req = request or ""
    norm = _normalize(req)
    words = norm.split()
    dangerous = [msg for pat, msg in _DANGEROUS if re.search(pat, norm, re.I)]

    intents = _intents(norm)
    primary = intents[0]["type"] if intents else ""
    ambiguous = len(words) < 4 or (not intents and len(words) < 6)

    missing = [label for pat, label in _ESSENTIALS.get(primary, [])
               if not re.search(pat, norm, re.I)]

    warnings = []
    if primary and missing and len(missing) >= 2:
        warnings.append("اطلاعات ناقص؛ برنامه پیش از تولید باید "
                        "این‌ها را با کاربر شفاف کند: "
                        + "، ".join(missing))
    if len(norm) > 4000:
        warnings.append("درخواست طولانی؛ فقط بخش اول وارد برنامه می‌شود")
    if re.search(r"https?://", norm):
        warnings.append("لینک/URL دارد — پس از تأیید برسی شود؛ "
                        "اجرای خودکار آن‌ها وجود ندارد")

    allowed = not dangerous
    if not allowed:
        note = "درخواست رد شد: " + "؛ ".join(dangerous)
    elif ambiguous:
        note = (f"درخواست مبهم؛ نوع حدسی «{primary or 'نامشخص'}» — "
                "نیازمند تأیید کاربر پیش از تولید")
    elif missing:
        note = (f"نوع: {primary} — شناسایی شد؛ اطلاعات ناقص: "
                + "، ".join(missing))
    elif primary:
        note = f"نوع: {primary} — از درخواست استخراج شد؛ ناقص ندارد"
    else:
        note = "نوع از کلمات درخواست تشخیص داده نشد؛ پرسش بیشتر لازم است"
    return {
        "ok": True, "allowed": allowed, "dangerous": dangerous,
        "ambiguous": ambiguous, "intents": intents,
        "primary_type": primary, "missing": missing,
        "warnings": warnings, "note": note,
    }


def guard(request: str) -> str | None:
    """Return a Persian reason when the request must be refused outright."""
    a = analyze(request)
    return None if a["allowed"] else a["note"]
