"""AF-31 — rational decision policy shared by Chat, Planner and Pipeline.

Classifies an ambiguous/important request into one honest decision:
    PROCEED  routine, low-risk work
    ASSUME   minor ambiguity — decide and announce the assumption
    ASK      material ambiguity — ask one compact, option-based question
    CONFIRM  sensitive/irreversible action — ActionGate must confirm

No new UI and no new API contract: the decision is recorded in the plan and in
memory, and is injected into the model's context through the existing
perception block.  It does not bypass ActionGate — CONFIRM only *requests* it.
"""
from __future__ import annotations

import re

PROCEED, ASSUME, ASK, CONFIRM = "PROCEED", "ASSUME", "ASK", "CONFIRM"

# AF-53 — only an ACTION that changes the world needs explicit consent.
# Merely mentioning security/token/password in a goal must not park the task:
# that produced many needless questions in the old Workspace.
_SENSITIVE = re.compile(
    r"(?i)(\bpush\b|\bdeploy\b|\bpublish\b|\brelease\b|"
    r"انتشار\s*(?:بده|دهیم|کن|اجرا)|استقرار\s*(?:کن|دهیم)?|"
    r"بارگذاری\s*(?:کن|دهیم)|"
    r"\bdelete\b|\bremove\b|حذف\s*(?:کن|شود|شان)|پاک\s*کن|"
    r"\breset\b|بازنشانی\s*کن|"
    r"پرداخت\s*(?:کن|انجام)|مهاجرت\s*(?:کن|انجام)|"
    r"force\s*(?:push|delete))")

_VAGUE = re.compile(
    r"(?i)^(committed|done|تمام|تمامش\s*کن|همین\s*طور|ادامه\s*بده|"
    r"یک\s*چیزی|یه\s*چیزی|بهش\s*رسیدگی\s*کن|درستش\s*کن|ok\b|باشه)$")

_ASSUME_HINT = re.compile(r"(?i)(بهتر| زیبا|سریع|کامل|کمی|کمتر|بیشتر|maybe|sh?-?could)")


def assess(goal: str, context: dict | None = None) -> dict:
    """Return a single honest decision with an optional option-based question."""
    text = str(goal or "").strip()
    ctx = context or {}
    if not text:
        return {"decision": ASK, "question": "هدف موردنظر را مشخص کنید.",
                "options": [], "recommended": "", "reason": "درخواست خالی است"}

    sensitive = _SENSITIVE.search(text)
    if sensitive:
        action = sensitive.group(1).strip()
        return {"decision": CONFIRM,
                "question": ("عملیات «%s» تأثیر خارج از پروژه یا غیرقابل بازگشت دارد. "
                             "چه کاری انجام شود؟" % action),
                "options": ["فقط پیشنمایش", "انجام بده", "انجام نده"],
                "recommended": "فقط پیشنمایش",
                "reason": "عملیات پرخطر «%s» — تأیید صریح لازم است" % action}

    if len(text) < 12 or _VAGUE.match(text):
        ptype = str(ctx.get("type") or ctx.get("project_type") or "پروژه")
        return {"decision": ASK,
                "question": "منظور دقیق شما چیست؟ لطفاً یکی از گزینهها را انتخاب کنید.",
                "options": ["بررسی و رفع مشکلات " + ptype,
                            "اضافهکردن قابلیت جدید", "تست و تحویل نسخه"],
                "recommended": "بررسی و رفع مشکلات " + ptype,
                "reason": "اطلاعات ضروری برای شروع ایمن کافی نیست"}

    if _ASSUME_HINT.search(text):
        assumption = "منظور بهبود محدود و برگشتپذیر همان سطح فعلی است"
        return {"decision": ASSUME, "question": "", "options": [],
                "recommended": "", "assumption": assumption,
                "reason": "ابهام کمخطر — تصمیم گرفته و فرض اعلام میشود"}

    return {"decision": PROCEED, "question": "", "options": [],
            "recommended": "", "reason": "درخواست مشخص و کمخطر است"}


def needs_user(decision: dict) -> bool:
    return decision.get("decision") in (ASK, CONFIRM)


def correct(user_claim: str, evidence: dict | None) -> dict:
    """Respectfully correct a wrong user claim using REAL evidence.

    Blind agreement is not allowed; when evidence is absent, say so honestly.
    """
    claim = str(user_claim or "")
    ev = evidence or {}
    ver = ev.get("verification")
    status = str((ver.get("status") if isinstance(ver, dict) else None) or
                 ev.get("status") or "")
    positive = re.search(r"(?i)(سبز|موفق|ok|passed|done|green|تمام)", claim)
    if positive and status and status not in ("PASS", "ok", "succeeded"):
        return {"agree": False,
                "reply": ("با احترام، شواهد فعلی موفقیت را تأیید نمیکند "
                          "(وضعیت واقعی: %s). پیش از اعلام موفقیت آن را بررسی میکنم." % status)}
    if positive and not status:
        return {"agree": False,
                "reply": "شاهدی برای این ادعا در دسترس نیست؛ آن را بررسی میکنم."}
    return {"agree": True, "reply": ""}
