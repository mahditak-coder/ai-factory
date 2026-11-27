"""AF-23 — error diagnosis: map a real error to an honest Persian reason.

Rules:
  * The reason ALWAYS comes from the real status/tag/message we actually
    received — never invented, never defaulted to "network".
  * When the evidence only allows a probability, the response says
    "احتمالاً" (likely) and keeps confidence="احتمالی".
  * Nothing secret is ever echoed: keys/tokens/headers are never part of
    the input to this module (server-side only paths) and the explanation
    contains no credential material.
"""

import re

_SECRET_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"), "<redacted-key>"),
    (re.compile(r"\br?sk-[A-Za-z0-9_\-]{8,}\b"), "<redacted-key>"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer <redacted>"),
    (re.compile(r"(?i)\b(api[_-]?key|token|password|secret|authorization)"
                r'(\s*[=:]\s*)"?([A-Za-z0-9._\-]{8,})"?'),
     r"\1\2<redacted>"),
)


def redact(text: str) -> str:
    """AF-23 (security rule): never echo credential material in any UI or
    log output — even if a provider itself echoes the key back."""
    out = str(text or "")
    for pat, repl in _SECRET_PATTERNS:
        out = pat.sub(repl, out)
    return out

# (pattern on lowercase tag+message) → Persian reason
_TAG_RULES: tuple[tuple[str, str], ...] = (
    ("api key", "کلید دسترسی Provider معتبر نیست"),
    ("apikey", "کلید دسترسی Provider معتبر نیست"),
    ("unauthorized", "کلید دسترسی Provider معتبر نیست"),
    ("authentication", "احراز هویت با Provider انجام نشد"),
    ("insufficient", "Provider اعتبار کافی ندارد"),
    ("quota", "سهمیهٔ Provider به پایان رسیده است"),
    ("credit", "اعتبار حساب Provider کافی نیست"),
    ("rate limit", "سرویس موقتاً درخواست بیشتری نمی‌پذیرد (محدودیت درخواست)"),
    ("rate_limit", "سرویس موقتاً درخواست بیشتری نمی‌پذیرد (محدودیت درخواست)"),
    ("too many requests", "سرویس موقتاً درخواست بیشتری نمی‌پذیرد (محدودیت درخواست)"),
    ("not found", "مدل یا نشست درخواستی در دسترس نیست"),
    ("unknown model", "مدل انتخاب‌شده در دسترس نیست"),
    ("invalid model", "مدل انتخاب‌شده در دسترس نیست"),
    ("model", "مدل انتخاب‌شده در دسترس نیست"),
    ("timeout", "سرویس در زمان تعیین‌شده پاسخ نداد"),
    ("timed out", "سرویس در زمان تعیین‌شده پاسخ نداد"),
    ("connection refused", "ارتباط با سرویس مربوطه برقرار نشد (سرویس اجرا نیست)"),
    ("connection reset", "ارتباط با سرویس نیمه‌کاره قطع شد"),
    ("incomplete", "پاسخ جریان‌دار نیمه‌کاره قطع شد"),
    ("certificate", "ارتباط امن (TLS) با سرویس برقرار نشد"),
    ("tls", "ارتباط امن (TLS) با سرویس برقرار نشد"),
    ("ssl", "ارتباط امن (TLS) با سرویس برقرار نشد"),
    ("name or service not known", "آدرس سرویس قابل شناسایی نیست (DNS)"),
    ("getaddrinfo", "آدرس سرویس قابل شناسایی نیست (DNS)"),
    ("json", "پاسخ دریافتی قابل پردازش نیست (قالب نامعتبر)"),
    ("decode", "پاسخ دریافتی قابل پردازش نیست (قالب نامعتبر)"),
)

_STATUS_RULES: dict[int, str] = {
    401: "کلید دسترسی Provider معتبر نیست",
    402: "اعتبار حساب Provider کافی نیست",
    403: "دسترسی به این سرویس مجاز نیست (کلید یا تنظیمات)",
    404: "مدل یا نشست درخواستی در دسترس نیست",
    408: "سرویس در زمان تعیین‌شده پاسخ نداد",
    409: "تداخل وضعیت سرویس (پورت/نشست) — نیاز به بررسی پیکربندی",
    422: "درخواست برای این سرویس معتبر نیست",
    429: "سرویس موقتاً درخواست بیشتری نمی‌پذیرد (محدودیت درخواست/سهمیه)",
    500: "خطای داخلی سرویس",
    502: "سرویس میانی پاسخ معتبری نداد",
    503: "موتور اجرای مدل در دسترس نیست",
    504: "سرویس در زمان تعیین‌شده پاسخ نداد",
}


def _tag_hint(tag: str, message: str) -> str | None:
    hay = f"{tag} {message}".lower()
    for pat, reason in _TAG_RULES:
        if re.search(re.escape(pat), hay):
            return reason
    return None


def explain(status: int, message: str, tag: str = "",
            stage: str = "", service: str = "OpenCode") -> dict:
    """Build the structured, UI-safe diagnosis object."""
    reason = None
    confidence = "قطعی"
    hint = _tag_hint(tag or "", message or "")
    if hint:
        reason = hint
        # message-only matches (not tag) may be provider phrasing — keep honest
        if not tag:
            confidence = "احتمالی"
    elif status in _STATUS_RULES:
        reason = _STATUS_RULES[status]
    else:
        reason = "خطا رخ داد — علت دقیق از پاسخ سرویس مشخص نشد"
        confidence = "احتمالی"

    diag = {
        "reason": reason,
        "confidence": confidence,          # قطعی / احتمالی
        "stage": stage or "نامشخص",
        "service": service,
        "code": status,
        "tag": tag or "",
        "suggestion": _suggestion(status, reason),
        "detail": redact(message)[:400],   # جزئیات فنی جمع‌شونده — ماسک‌شده
    }
    return diag


def _suggestion(status: int, reason: str) -> str:
    if "معتبر نیست" in reason or "احراز هویت" in reason or "مجاز نیست" in reason:
        return "کلید Provider را در تنظیمات بررسی کنید و «تست اتصال» را بزنید."
    if "اعتبار" in reason or "سهمیه" in reason or "محدودیت" in reason:
        return "بعد از رفع محدودیت/شارژ حساب دوباره تلاش کنید یا Provider دیگری انتخاب کنید."
    if "مدل" in reason:
        return "مدل دیگری از همان Provider انتخاب کنید یا فهرست مدل‌ها را بازخوانی کنید."
    if "پاسخ نداد" in reason or "نیمه‌کاره" in reason:
        return "چند لحظه صبر کنید و «تلاش مجدد» را بزنید؛ اگر تکرار شد، وضعیت شبکه را بررسی کنید."
    if "اجرا نیست" in reason or "برقرار نشد" in reason:
        return "چند ثانیه بعد دوباره تلاش کنید — سرویس به‌طور خودکار بالا می‌آید."
    if "پاسخ دریافتی" in reason:
        return "«تلاش مجدد» را بزنید؛ اگر تکرار شد، این مدل ممکن است پاسخ ناسازگار بدهد."
    if "TLS" in reason or "DNS" in reason:
        return "اتصال اینترنت و DNS دستگاه را بررسی کنید."
    if status >= 500:
        return "چند لحظه بعد دوباره تلاش کنید؛ اگر تکرار شد لاگ سرور را از منوی لاگ‌ها ببینید."
    return "«تلاش مجدد» را بزنید؛ اگر تکرار شد از بخش جزئیات فنی اطلاعات بیشتری ببینید."
