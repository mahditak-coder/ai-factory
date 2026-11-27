"""AF NEXT Phase 5 — pure, evidence-only quality analyses.

These functions never touch the filesystem. They analyse ONLY real content
that Quality Gate obtained through governed ToolExecutor calls, and they
never invent a PASS where measurement was impossible.
"""
from __future__ import annotations

import re

PERSIAN = re.compile(r"[\u0600-\u06FF\u06F0-\u06F9]")
LOREM = re.compile(r"(lorem ipsum|dolor sit amet|TODO|FIXME|placeholder)", re.I)

# Per-engine, real, generous thresholds — measured values only.
ENGINE_BUDGET = {
    "android": {"build_ms": 420000, "artifact_mb": 12.0},
    "web_saas": {"build_ms": 180000, "artifact_mb": 5.0},
    "backend_api": {"build_ms": 180000, "artifact_mb": 5.0},
    "ai_agent": {"build_ms": 180000, "artifact_mb": 5.0},
    "game": {"build_ms": 180000, "artifact_mb": 8.0},
    "desktop": {"build_ms": 180000, "artifact_mb": 8.0},
}


def F(dimension, severity, code, message, evidence=None, status="FAIL"):
    return {"dimension": dimension, "severity": severity, "code": code,
            "message": message, "evidence": evidence or {}, "status": status}


def relevant_files(rels: list[str]) -> list[str]:
    keep = (".html", ".htm", ".css", ".js", ".py", "package.json")
    return [r for r in rels if r.endswith(keep) and
            not r.startswith((".factory", "__pycache__", "build/"))][:16]


def analyze_functionality(tests: dict, sec: dict) -> list[dict]:
    out = []
    rep = (tests.get("output") or {}) if isinstance(tests, dict) else {}
    if tests.get("status") != "PASS":
        out.append(F("functionality", "Critical", "tests-tool-blocked",
                     "ابزار اجرای تست در دسترس نبود",
                     {"status": tests.get("status"), "error": tests.get("error")},
                     tests.get("status", "BLOCKED")))
    elif not rep.get("ran"):
        out.append(F("functionality", "Critical", "no-test-coverage",
                     "هیچ تست واقعی اجرا نشد — PASS مجاز نیست",
                     {"note": rep.get("note")}))
    elif not rep.get("ok"):
        errs = ((rep.get("contract") or {}).get("errors") or [])[:3]
        out.append(F("functionality", "Critical", "functional-tests-failed",
                     "تستهای عملکردی پروژه شکست خورد", {"errors": errs}))
    if sec.get("status") != "PASS":
        out.append(F("security_privacy", "High", "security-tool-blocked",
                     "اسکن امنیت اجرا نشد", {"status": sec.get("status"),
                                             "error": sec.get("error")},
                     sec.get("status", "BLOCKED")))
    return out


def analyze_visual(files: dict[str, str], shot: dict) -> tuple[list[dict], dict]:
    out = []
    coverage = {"screenshot": shot.get("status", "BLOCKED"),
                "pixel_geometry": "NOT VERIFIED",
                "reason": shot.get("error") or "no real screenshot"}
    if shot.get("status") != "PASS":
        out.append(F("visual", "High", "screenshot-blocked",
                     "گرفتن Screenshot واقعی ممکن نشد؛ بررسی پیکسلی انجام نشد",
                     {"error": shot.get("error"),
                      "safe_path": "Add a narrowly-scoped preview.screenshot tool "
                                   "with fixed argv + in-project path, approved "
                                   "by SecurityPolicy (not done in P5)."},
                     "BLOCKED"))
    htmls = {k: v for k, v in files.items() if k.endswith((".html", ".htm"))}
    if not htmls:
        out.append(F("visual", "Medium", "no-renderable-page",
                     "هیچ صفحهٔ قابل render برای بررسی بصری یافت نشد", {},
                     "NOT VERIFIED"))
        return out, coverage
    blob = "\n".join(htmls.values())
    if "{{" in blob or "}}" in blob:
        out.append(F("visual", "High", "unfilled-placeholder",
                     "قالب پرنشده ({{...}}) در رابط باقی مانده است",
                     {"files": [k for k, v in htmls.items() if "{{" in v]}))
    if not PERSIAN.search(blob):
        out.append(F("visual", "Medium", "no-persian-text",
                     "هیچ متن فارسی در رابط یافت نشد", {}))
    if not re.search(r'dir\s*=\s*["\']rtl', blob):
        out.append(F("visual", "High", "rtl-missing",
                     "جهت RTL در رابط مشخص نشده است", {}))
    if "overflow:hidden" in blob.replace(" ", ""):
        out.append(F("visual", "Medium", "possible-clipping",
                     "overflow:hidden میتواند موجب برش محتوا شود (بصری NOT VERIFIED)",
                     {"method": "static-source-only"}))
    states = [s for s in ("loading", "empty", "خطا", "error", "success", "موفق")
              if s.lower() in blob.lower()]
    if len(states) < 2:
        out.append(F("visual", "Medium", "states-not-evidenced",
                     "حالتهای Loading/Empty/Error/Success در منبع یافت نشد",
                     {"found": states}, "NOT VERIFIED"))
    return out, coverage


def analyze_ux(files: dict[str, str], product_type: str = "") -> list[dict]:
    out = []
    htmls = {k: v for k, v in files.items() if k.endswith((".html", ".htm"))}
    if not htmls:
        out.append(F("real_ux", "Medium", "no-user-surface",
                     "سطح کاربری قابل بررسی وجود ندارد", {}, "NOT VERIFIED"))
        return out
    blob = "\n".join(htmls.values())
    n_controls = len(re.findall(r"<(button|a|input|form|select)\b", blob, re.I))
    if n_controls == 0:
        out.append(F("ui_ux", "High", "no-main-path",
                     "هیچ مسیر اصلی کاربر (دکمه/لینک/فرم) در رابط وجود ندارد",
                     {"controls": n_controls}))
    if not re.search(r"addEventListener|onclick|onChange|submit", blob, re.I):
        out.append(F("ui_ux", "Medium", "no-interaction-feedback",
                     "بازخورد پس از تعامل در منبع یافت نشد", {}))
    if not re.search(r"viewport", blob, re.I):
        out.append(F("ui_ux", "Medium", "mobile-viewport-missing",
                     "meta viewport وجود ندارد — تجربه موبیل NOT VERIFIED", {}))
    if not re.search(r"خطا|error|catch|retry|تلاش مجدد", blob, re.I):
        out.append(F("ui_ux", "Medium", "no-error-recovery",
                     "مدیریت خطا/بازیابی در رابط یافت نشد", {}, "NOT VERIFIED"))
    if LOREM.search(blob):
        out.append(F("ui_ux", "High", "untranslated-or-todo",
                     "متن ترجمهنشده یا TODO/lorem در رابط وجود دارد",
                     {"matches": LOREM.findall(blob)[:3]}))
    kid = any(w in product_type.lower() for w in ("kid", "child", "کودک"))
    if kid:
        if not re.search(r"کودک|بازی|آسان|ساده", blob):
            out.append(F("real_ux", "High", "not-child-friendly",
                         "محصول کودکانه فاقد راهنمایی ساده/سنمناسب است", {}))
    else:
        out.append(F("real_ux", "Medium", "child-ux-skipped",
                     "بررسی تجربه کودکان در این محصول اعمالنشد", {}, "SKIPPED"))
    out.append(F("real_ux", "Low", "human-session-not-verified",
                 "کیفیت ذهنی تجربه کاربر نیازمند جلسه انسانی است",
                 {}, "NOT VERIFIED"))
    return out


def analyze_accessibility(files: dict[str, str]) -> list[dict]:
    out = []
    blob = "\n".join(files.values())
    if not blob:
        return [F("accessibility", "Medium", "no-a11y-surface",
                  "منبعی برای بررسی دسترسپذیری یافت نشد", {}, "NOT VERIFIED")]
    if not re.search(r'lang\s*=\s*["\']fa', blob):
        out.append(F("accessibility", "Medium", "lang-not-fa",
                     "زبان سند بهعنوان fa مشخص نشده است", {}))
    if not re.search(r"\b(aria-|role=|<label)", blob, re.I):
        out.append(F("accessibility", "Medium", "no-labels-or-aria",
                     "برچسب/aria در رابط یافت نشد", {}))
    if not re.search(r"font-size|rem\b|px\b", blob):
        out.append(F("accessibility", "Low", "typography-not-evidenced",
                     "اندازه قلم در منبع مشخص نشده است", {}, "NOT VERIFIED"))
    out.append(F("accessibility", "Low", "screenreader-not-verified",
                 "تست صفحهخوان واقعی در Termux NOT VERIFIED", {}, "NOT VERIFIED"))
    out.append(F("accessibility", "Low", "touch-target-not-verified",
                 "اندازه ناحیه لمس بدون render پیکسلی NOT VERIFIED", {}, "NOT VERIFIED"))
    return out


def analyze_code(files: dict[str, str]) -> list[dict]:
    out = []
    blobs = {k: v for k, v in files.items() if k.endswith((".py", ".js", ".html"))}
    if not blobs:
        return [F("code_architecture", "Medium", "no-source",
                  "منبعی برای ارزیابی کیفیت کد یافت نشد", {}, "NOT VERIFIED")]
    blob = "\n".join(blobs.values())
    if re.search(r"\beval\s*\(|\bexec\s*\(", blob):
        out.append(F("code_architecture", "High", "dynamic-execution",
                     "eval/exec در کد منبع یافت شد", {}))
    if len(blobs) == 1 and len(blob) > 6000:
        out.append(F("code_architecture", "Low", "single-large-module",
                     "تمام منطق در یک فایل بزرگ قرار دارد", {"bytes": len(blob)}))
    if not any(k.startswith("tests/") for k in files):
        out.append(F("code_architecture", "Medium", "no-collocated-tests",
                     "تست همر-building در پروژه یافت نشد", {}, "NOT VERIFIED"))
    return out


def analyze_media(files: dict[str, str]) -> list[dict]:
    media = [k for k in files if k.lower().endswith((
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp3", ".ogg", ".wav", ".mp4"))]
    if not media:
        return [F("media", "Low", "no-media-assets",
                  "دارایی صوتی/تصویریای وجود ندارد — این بُعد SKIPPED است",
                  {}, "SKIPPED")]
    return [F("media", "Low", "media-inventory",
              "داراییreal فهرست شد؛ کیفیت ادراکی بدون ابزار NOT VERIFIED",
              {"files": media[:8]}, "NOT VERIFIED")]


def analyze_performance(engine: str, build_ms: int | None,
                        tool_ms: dict[str, int], sizes: dict[str, int]) -> list[dict]:
    out = []
    budget = ENGINE_BUDGET.get(engine, ENGINE_BUDGET["web_saas"])
    if build_ms is not None:
        if build_ms > budget["build_ms"]:
            out.append(F("performance", "High", "build-slow",
                         "زمان Build ازbudget تعریفشده فراتر رفت",
                         {"measured_ms": build_ms, "budget_ms": budget["build_ms"]}))
        else:
            out.append(F("performance", "Low", "build-measured",
                         "زمان Build اندازهگیری شد",
                         {"measured_ms": build_ms, "budget_ms": budget["build_ms"]},
                         "PASS"))
    else:
        out.append(F("performance", "Medium", "build-not-measured",
                     "زمان Build اندازهگیری نشد", {}, "NOT VERIFIED"))
    for tool, ms in (tool_ms or {}).items():
        out.append(F("performance", "Low", "tool-duration",
                     "مدت اجرای ابزار مجاز اندازهگیری شد",
                     {"tool": tool, "duration_ms": ms}, "PASS"))
    total_mb = round(sum(sizes.values()) / (1024 * 1024), 3) if sizes else None
    if total_mb is not None:
        if total_mb > budget["artifact_mb"]:
            out.append(F("performance", "High", "artifact-too-large",
                         "اندازه خروجی ازbudget موتور فراتر رفت",
                         {"measured_mb": total_mb, "budget_mb": budget["artifact_mb"]}))
        else:
            out.append(F("performance", "Low", "artifact-measured",
                         "اندازه خروجی اندازهگیری شد",
                         {"measured_mb": total_mb, "budget_mb": budget["artifact_mb"]},
                         "PASS"))
    out.append(F("performance", "Medium", "memory-cpu-not-measured",
                 "مصرف RAM/CPU و رفتار کمبود منابع از طریق ابزار مجاز اندازهگیری نشد",
                 {}, "NOT VERIFIED"))
    return out


def analyze_security(sec: dict) -> list[dict]:
    out = []
    rep = (sec.get("output") or {}) if isinstance(sec, dict) else {}
    if sec.get("status") != "PASS":
        return [F("security_privacy", "High", "security-scan-unavailable",
                  "اسکن امنیت اجرا نشد", {"status": sec.get("status"),
                                          "error": sec.get("error")}, "BLOCKED")]
    if rep.get("blocked") or rep.get("critical"):
        out.append(F("security_privacy", "Critical", "security-gate-blocked",
                     "ایجاد بحرانی امنیتی مانع تحویل است",
                     {"critical": (rep.get("critical") or [])[:3],
                      "files_scanned": rep.get("files_scanned")}))
    else:
        out.append(F("security_privacy", "Low", "security-scan-clean",
                     "اسکن امنیتی مجاز بدون یافته بحرانی",
                     {"files_scanned": rep.get("files_scanned")}, "PASS"))
    out.append(F("security_privacy", "Low", "privacy-review-not-verified",
                 "بررسی حریم خصوصی در سطح انسانی NOT VERIFIED", {}, "NOT VERIFIED"))
    return out
