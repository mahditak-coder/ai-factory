"""Smart Release — AF-24: one Persian request → a verified, delivered release.

This is a NEW orchestration layer, not a rewrite. Every heavy step delegates
to proven infrastructure already in the factory:

    type detection   → real file structure (+ meta.json, never folder name)
    preflight        → core.engines.android.AndroidEngine / core.deps / shutil.which
    build            → AndroidEngine.build(sign=True) | core.release.build_release
    test             → engine.verify_artifacts | core.factory.testrun_combined
    security         → core.security_gate.scan (same gate as the release API)
    delivery         → SHA256 + local validation + safe copy to ~/storage/downloads
    git + GitHub     → core.git_sync (only allowed files, no force, verified heads)
    history/report   → core.factory.append_history + data/smart_releases.json

Failure is always reported as failure. Nothing that was not executed is ever
reported as done, and no secret/key material is ever written to a report.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import zipfile
from datetime import datetime
from pathlib import Path

import core.config as config
from core import factory, git_sync, paths, release as releasemod

log = __import__("logging").getLogger("ai-factory.smart-release")

# honest machine statuses + their Persian labels used in the delivered report
OK, FAIL, PARTIAL, SKIP, UNVERIFIED = "ok", "failed", "partial", "not_done", "unverified"
AWAIT = "awaiting_confirmation"      # AF-25: a proposal is waiting for the user
LABELS = {OK: "موفق", FAIL: "ناموفق", PARTIAL: "ناقص",
          SKIP: "انجام\u200cنشده", UNVERIFIED: "تأییدنشده",
          AWAIT: "در انتظار تأیید"}

_STORE = config.PROJECT_ROOT / "data" / "smart_releases.json"
_NAME_RE = re.compile(config.PROJECT_NAME_RE)

_NORM = {"ك": "ک", "ي": "ی", "ؤ": "و", "إ": "ا", "أ": "ا", "ة": "ه",
         "0": "۰", "1": "۱", "2": "۲", "3": "۳", "4": "۴",
         "5": "۵", "6": "۶", "7": "۷", "8": "۸", "9": "۹"}
# Only digits are converted back for latin/digit keyword matching: reversing
# the letter map would corrupt Persian words (ا ← أ) and break intent detection.
_UNORM = {v: k for k, v in _NORM.items() if k.isdigit()}

# Weighted, local (offline) intent signals — no model call needed to decide
# whether the user is asking for a FINAL release operation.
_RELEASE_SIGNALS = [
    (5, r"انتشار|منتشر\s*کن|ریلیز|release\s*کن| Smart\s*Release"),
    (5, r"نسخه[ٔ\s]*(نهایی|نهایی\s*را)|آخرین\s*نسخه[ٔ\s]*(سالم|نهایی)"),
    (4, r"بساز\s*و\s*(ذخیره|دانلود)|_builder.*deliver"),
    (4, r"دانلودها|دانلود\s*کن|پوشه[ٔ\s]*دانلود|Downloads"),
    (3, r"\bgit\b|گیت"),
    (3, r"\bgithub\b|گیت\s*هاب|گیت هاب"),
    (3, r"همگام|به[\s]?روز\s*کن|آپدیت\s*کن|سینک\s*کن"),
    (2, r"بررسی\s*کن.*(بساز|نسخه)|چک\s*کن.*(بساز|نسخه)"),
    (2, r"خروجی\s*(نهایی|قابل\s*تحویل)|بسته[ٔ\s]*نهایی"),
]
_BUILD_SIGNALS = (r"بساز|ساخت|build|کامپایل|generate|تولید")
_ONLY_ADVICE_RE = re.compile(r"چطور|چگونه|دستور|روش|راهنما|بگو|توضیح\s*ده|آموزش")


def _norm(text: str) -> str:
    text = str(text or "")
    for k, v in _NORM.items():
        text = text.replace(k, v)
    return text


def _plain(text: str) -> str:
    """Latin/digit-normalised text for keyword matching."""
    t = _norm(text)
    for k, v in _UNORM.items():
        t = t.replace(k, v)
    return t.lower()


def detect_intent(text: str) -> dict:
    """Decide whether a Persian request means 'do a final Smart Release'.

    Returns {is_release, score, signals, wants_git, wants_download,
    wants_build, advice_only, note}. Purely local and deterministic.
    """
    raw = _norm(text)
    t = _plain(text)
    score, signals = 0, []
    for weight, pattern in _RELEASE_SIGNALS:
        if re.search(pattern, raw, re.I) or re.search(pattern, t, re.I):
            score += weight
            signals.append(pattern)
    wants_git = bool(re.search(r"\bgit\b|گیت(?!\s*هاب)|github|گیت\s*هاب|گیت هاب", t, re.I))
    wants_download = bool(re.search(r"دانلود|Downloads|storage|ذخیره", raw, re.I))
    wants_build = bool(re.search(_BUILD_SIGNALS, raw, re.I))
    advice_only = bool(_ONLY_ADVICE_RE.search(raw))
    # "build it and deliver" needs ≥2 independent release signals; explicit
    # انتشار/ریلیز alone is enough because it is unambiguous.
    explicit = bool(re.search(r"انتشار|منتشر\s*کن|ریلیز|Smart\s*Release", t, re.I))
    is_release = (score >= 8 or (explicit and score >= 5)) and not advice_only
    return {
        "is_release": bool(is_release), "score": score,
        "signals": [s[:80] for s in signals[:8]],
        "wants_git": wants_git, "wants_download": wants_download,
        "wants_build": wants_build, "advice_only": advice_only,
        "note": ("درخواست انتشار نهایی شناسایی شد" if is_release else
                 ("درخواست صرفاً توضیحی است — عملیات اجرا نمیشود"
                  if advice_only else "قصد انتشار نهایی تشخیص داده نشد")),
    }


def project_names() -> list[str]:
    out = []
    try:
        for d in config.PROJECTS_DIR.iterdir():
            if d.is_dir() and not d.name.startswith(".") and _NAME_RE.match(d.name):
                out.append(d.name)
    except OSError:
        pass
    return sorted(out)


def resolve_project(text: str = "", project: str | None = None) -> str:
    """Find the real project from an explicit name or a natural-language hint."""
    if project:
        p = str(project).strip()
        d = paths.project_dir(p)          # validates + requires existence
        return d.name
    names = project_names()
    raw = _norm(text)
    exact = [n for n in names if n in raw or _norm(n) in raw]
    if len(exact) == 1:
        return exact[0]
    # Persian display names from meta.json (e.g. «معلم من» → teacher-android)
    hits = []
    for n in names:
        try:
            meta = factory.read_meta(config.PROJECTS_DIR / n)
        except Exception:  # noqa: BLE001 — a broken meta must not break lookup
            continue
        disp = str(meta.get("name") or "")
        if disp and (disp in raw or _norm(disp) in raw):
            hits.append(n)
    hits = list(dict.fromkeys(hits + exact))
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise ValueError("چند پروژه با این درخواست مطابقت دارند: " + ", ".join(hits[:8]))
    raise ValueError("پروژه در درخواست مشخص نشد یا یافت نشد؛ نام پروژه را بدهید "
                     "(مثال: teacher-android)")


def detect_type(d: Path) -> dict:
    """Type from REAL structure (meta.json is evidence, never the sole judge)."""
    d = Path(d)
    evidence: list[str] = []
    meta = {}
    try:
        meta = factory.read_meta(d)
    except Exception:  # noqa: BLE001
        pass
    declared = str(meta.get("type") or "")
    has_manifest = (d / "AndroidManifest.xml").is_file()
    has_java = (d / "java").is_dir()
    has_pkg = (d / "package.json").is_file()
    has_server_py = (d / "server.py").is_file()
    has_req = (d / "requirements.txt").is_file()
    has_index = (d / "index.html").is_file()
    if has_manifest or has_java:
        evidence.append("AndroidManifest.xml/java")
    if has_pkg:
        evidence.append("package.json")
    if has_server_py or has_req:
        evidence.append("server.py/requirements.txt")
    if has_index:
        evidence.append("index.html")
    if declared:
        evidence.append("meta.json.type=" + declared)

    if has_manifest and has_java or declared in ("android", "android-app"):
        kind = "android"
    elif has_pkg and not has_server_py:
        kind = "node"
    elif has_server_py or has_req or declared in ("api", "ai-tool", "fullstack", "fullstack-python"):
        kind = "python"
    elif has_index or declared in releasemod.STATIC_TYPES:
        kind = "web"
    else:
        kind = "other"
    return {"kind": kind, "declared": declared, "evidence": evidence,
            "confidence": "بالا" if len(evidence) >= 2 else "متوسط",
            "note": "تشخیص بر اساس ساختار واقعی فایلها، نه نام پوشه" if evidence
                    else "هیچ نشانهٔ ساختاری یافت نشد — بستهٔ عمومی منبع"}


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _downloads_dir(override: str | None = None) -> Path | None:
    if override:
        p = Path(override).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
            return p if os.access(p, os.W_OK) else None
        except OSError:
            return None
    cands = [Path.home() / "storage" / "downloads",
             Path("/sdcard/Download"), Path("/storage/emulated/0/Download")]
    for p in cands:
        try:
            if p.is_dir() and os.access(p, os.W_OK):
                return p
        except OSError:
            continue
    return None


def _safe_name(project: str, version: str, ext: str) -> str:
    base = re.sub(r"[^\w\u0600-\u06FF.\-]+", "-", str(project).strip())
    base = base.strip("-.") or "project"
    ver = re.sub(r"[^\w.\-]", "-", str(version)).strip("-.")
    return "%s-%s%s" % (base, ver or "release", ext)


def _deliver(artifact: Path, project: str, version: str,
             downloads_dir: str | None = None) -> dict:
    """Copy into Downloads WITHOUT ever replacing a different existing file."""
    dest_dir = _downloads_dir(downloads_dir)
    if dest_dir is None:
        return {"status": FAIL, "path": None,
                "note": "پوشه Downloads در دسترس نیست (termux-setup-storage لازم است)"}
    src_sha = _sha256(artifact)
    name = _safe_name(project, version, artifact.suffix)
    target = dest_dir / name
    n = 1
    while target.exists():
        if _sha256(target) == src_sha:
            return {"status": OK, "path": str(target), "name": target.name,
                    "sha256": src_sha, "size_bytes": target.stat().st_size,
                    "reused": True, "overwrite_protected": False,
                    "note": "فایل با هش یکسان از قبل موجود است — کپی تکراری نشد"}
        n += 1
        stem = name[:-len(artifact.suffix)] if artifact.suffix else name
        target = dest_dir / ("%s-%d%s" % (stem, n, artifact.suffix))
        if n > 50:
            return {"status": FAIL, "path": None, "note": "نام جایگزین یافت نشد"}
    try:
        shutil.copyfile(artifact, target)
    except OSError as exc:
        return {"status": FAIL, "path": None, "note": "کپی ناموفق: %s" % exc}
    ok = target.is_file() and _sha256(target) == src_sha
    return {"status": OK if ok else FAIL, "path": str(target) if ok else None,
            "name": target.name, "sha256": src_sha,
            "size_bytes": target.stat().st_size if target.is_file() else 0,
            "reused": False, "overwrite_protected": n > 1,
            "note": ("فایل با هش تأییدشده ذخیره شد" if ok else
                     "پس از کپی هش مطابقت نداشت")}


def _validate_artifact(path: Path, kind: str) -> dict:
    """Real integrity checks, tailored to the artifact kind."""
    path = Path(path)
    checks: dict[str, bool] = {}
    if not path.is_file() or path.stat().st_size == 0:
        return {"ok": False, "checks": {}, "problems": ["فایل خروجی وجود ندارد یا خالی است"]}
    sha = _sha256(path)
    if kind == "android":
        try:
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
                checks["zip_integrity"] = zf.testzip() is None
                checks["android_manifest"] = "AndroidManifest.xml" in names
                checks["classes_dex"] = "classes.dex" in names
                checks["signature"] = any(
                    n.startswith("META-INF/") and n.upper().endswith((".RSA", ".DSA", ".EC"))
                    for n in names)
        except (zipfile.BadZipFile, OSError):
            checks["zip_integrity"] = False
    else:
        try:
            with zipfile.ZipFile(path) as zf:
                checks["zip_integrity"] = zf.testzip() is None
                checks["has_files"] = len(zf.namelist()) > 0
        except (zipfile.BadZipFile, OSError):
            checks["zip_integrity"] = False
    problems = [k for k, v in checks.items() if not v]
    return {"ok": bool(checks) and not problems, "checks": checks,
            "problems": problems, "sha256": sha,
            "size_bytes": path.stat().st_size}


def _load_reports() -> list[dict]:
    try:
        d = json.loads(_STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _save_reports(reports: list[dict]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reports[-200:], ensure_ascii=False, indent=1),
                   encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(_STORE)


def last(project: str | None = None) -> dict | None:
    recs = _load_reports()
    if project:
        recs = [r for r in recs if r.get("project") == project]
    return dict(recs[-1]) if recs else None


def list_reports(project: str | None = None) -> list[dict]:
    recs = _load_reports()
    if project:
        recs = [r for r in recs if r.get("project") == project]
    return list(reversed(recs))


def _append_report(rec: dict) -> dict:
    """Append-only, de-duplicated: a repeated identical run adds no record."""
    recs = _load_reports()
    sig_keys = ("project", "version", "artifact_sha256", "git_head_local", "status")
    sig = tuple(rec.get(k) for k in sig_keys)
    for old in reversed(recs):
        if tuple(old.get(k) for k in sig_keys) == sig:
            rec["reused_record"] = True
            rec["report_file"] = str(_STORE)
            return rec
    rec["reused_record"] = False
    recs.append(rec)
    _save_reports(recs)
    rec["report_file"] = str(_STORE)
    return rec


def plan(project: str, text: str = "") -> dict:
    """Read-only preflight: what WOULD happen, with real evidence."""
    d = paths.project_dir(project)
    typ = detect_type(d)
    meta = factory.read_meta(d)
    version = releasemod.project_version(d)
    if typ["kind"] == "android":
        from core.engines import android as andmod
        eng = andmod.AndroidEngine()
        tools = eng.available()
        pkg = str(meta.get("package") or "")
        key_present = bool(pkg) and eng.key_path(d).is_file() and \
            (andmod.PRODUCT_KEYS / (pkg + ".env")).is_file()
        build_method = ("core.engines.android.AndroidEngine.build(sign=True)"
                        if key_present else "متوقف: کلید امضای محصول موجود نیست")
        test_method = "ENGINE verify_artifacts (badging + classes.dex + apksigner)"
    else:
        tools = {"python3": bool(shutil.which("python3")),
                 "node": bool(shutil.which("node")),
                 "zipfile": True}
        key_present = None
        build_method = ("core.release.build_release → release ZIP"
                        if typ["kind"] != "other" else
                        "core.exporter.export → بستهٔ منبع تمیز (نوع نامشخص)")
        test_method = "core.factory.testrun_combined (unittest + قرارداد نوع)"
    git = git_sync.inspect(project)
    from core import security_gate
    gate = security_gate.scan(project, mode="release")
    risks = []
    if git.get("repo") and git.get("public"):
        risks.append("مخزن GitHub عمومی است — هر محتوای خصوصی/کتاب انتشار را متوقف میکند")
    if git.get("repo") and (git.get("ahead") or git.get("behind")):
        risks.append("تاریخچهٔ محلی و آنلاین هم\u200cخوان نیست — بررسی تعارض لازم است")
    if typ["kind"] == "android" and not key_present:
        risks.append("کلید امضای محصول یافت نشد — امضا با هویت فعلی ممکن نیست")
    if gate.get("blocked"):
        risks.append("دروازهٔ امنیت محتوا یافتهٔ بحرانی دارد — انتشار متوقف میشود")
    if not git.get("repo"):
        risks.append("پروژه داخل مخزن Git نیست — همگامسازی انجامنشده خواهد بود")
    elif git.get("inside_ai_factory_root") or not git.get("project_is_repo_root"):
        risks.append("مخزن شناسایشده ریشهٔ AI Factory است، نه مخزن اختصاصی پروژه — "
                     "ارسال متوقف میشود تا پروژهها/تغییرات کاربر ناخواسته منتشر نشوند")
    if git.get("repo") and not git.get("upstream"):
        risks.append("شاخه فاقد Remote/upstream است — ارسال به GitHub متوقف یا انجامنشده است")
    return {
        "ok": True, "project": project, "project_root": str(d),
        "display_name": str(meta.get("name") or project),
        "type": typ, "version": version,
        "tools": tools, "build_method": build_method, "test_method": test_method,
        "signing_key_present": key_present,
        "deliver_to": str(_downloads_dir() or "در دسترس نیست"),
        "git": git, "security": {"ok": not gate.get("blocked"),
                                 "critical": len(gate.get("critical", [])),
                                 "warnings": len(gate.get("warnings", [])),
                                 "scanned": gate.get("files_scanned", 0)},
        "risks": risks,
        "requires_confirm": bool(risks),
        "detected_intent": detect_intent(text) if text else None,
    }


def _fail(_project: str, stage: str, reason: str, **extra) -> dict:
    # `_project` is deliberately not named `project`: callers pass **result,
    # which already carries a `project` key, without a duplicate-argument bug.
    rec = {"status": FAIL, "project": _project, "stage": stage,
           "note": str(reason)[:600], "finished": datetime.now().isoformat(timespec="seconds")}
    rec.update(extra)
    rec["status_label"] = LABELS[FAIL]
    rec["summary_fa"] = ("پروژه: %s | مرحله: %s | وضعیت: ناموفق\n%s"
                         % (_project, stage, rec["note"]))
    factory.append_history(_project, "smart-release-failed", "%s: %s" % (stage, rec["note"][:160]))
    return _append_report(rec)


def run(project: str | None = None, text: str = "", confirm: bool = False,
        version: str | None = None, push: bool = True,
        downloads_dir: str | None = None,
        auto_git: bool = True, git_decision: dict | None = None,
        decline_git: bool = False, session_id: str = "") -> dict:
    """Execute the full Smart Release. Always returns an honest report."""
    try:
        project = resolve_project(text, project)
    except (ValueError, PermissionError, FileNotFoundError) as exc:
        return {"status": FAIL, "stage": "project", "note": str(exc),
                "project": project, "finished": datetime.now().isoformat(timespec="seconds")}
    d = paths.project_dir(project)
    meta = factory.read_meta(d)
    p = plan(project, text)
    previous = last(project)
    version_before = (previous or {}).get("version") or None

    # Explicit version changes only (AF-12: never auto-incremented).
    if version and str(version).strip() != p["version"]:
        if not confirm:
            return _fail(project, "version", "تغییر نسخه نیازمند confirm صریح است",
                         version_before=version_before)
        try:
            releasemod.set_version(project, str(version).strip())
            p = plan(project, text)
        except ValueError as exc:
            return _fail(project, "version", str(exc))

    result = {
        "status": FAIL, "project": project,
        "display_name": p["display_name"],
        "project_root": str(d),
        "project_type": p["type"]["kind"], "type_evidence": p["type"]["evidence"],
        "version_before": version_before, "version": p["version"],
        "build": {"status": SKIP, "method": p["build_method"]},
        "tests": {"status": SKIP, "method": p["test_method"]},
        "signature": {"status": SKIP},
        "artifact": None, "downloads": {"status": SKIP},
        "git": {"status": SKIP}, "github": {"status": SKIP},
        "git_prepare": {"status": SKIP},
        "risks": p["risks"], "steps": [], "started": datetime.now().isoformat(timespec="seconds"),
        "generator": "AI Factory Smart Release (AF-24 + AF-25 autonomous Git/GitHub)",
    }

    def step(name: str, ok: bool, detail: str) -> None:
        result["steps"].append({"step": name, "ok": bool(ok), "detail": str(detail)[:260]})

    # 1) security gate — fail closed, exactly as the existing release API does
    if not p["security"]["ok"]:
        step("security-gate", False, "یافتهٔ بحرانی — انتشار متوقف شد")
        return _fail(project, "security", "دروازهٔ امنیت محتوا انتشار را متوقف کرد",
                     **result)
    step("security-gate", True, "%d فایل اسکن شد" % p["security"]["scanned"])

    # 1.5) AUTONOMOUS Git/GitHub preparation (AF-25) — BEFORE build/deliver/sync.
    # A missing repo/remote produces a real proposal + one-shot confirmation;
    # no build or delivery happens until the user confirms in the conversation.
    if auto_git:
        from core import project_git
        try:
            gp = project_git.ensure_ready(project, decision=git_decision,
                                          decline=decline_git, session_id=session_id)
        except Exception as exc:  # noqa: BLE001 — honest, never fake ready
            gp = {"status": FAIL, "note": "آمادهسازی Git/GitHub ناموفق: %s" % exc}
        result["git_prepare"] = {
            "status": gp["status"], "note": gp.get("note", ""),
            "created": gp.get("created", False),
            "connected": gp.get("connected", False),
            "repo": gp.get("repo"), "url": gp.get("url"),
            "proposal": gp.get("proposal"),
        }
        step("git-prepare", gp["status"] in (OK, SKIP),
             gp.get("note", "") or gp["status"])
        if gp["status"] == project_git.NEEDS_CONFIRM:
            from core import confirmations
            prop = gp["proposal"]
            payload = {"project": project, "action": "create-github-repo",
                       "name": prop["proposed_name"],
                       "private": prop["private_default"], "create_repo": True}
            cf = confirmations.create(project, "create-github-repo", payload,
                                      prop["question_fa"], session_id)
            result.update({
                "status": AWAIT, "status_label": LABELS[AWAIT],
                "confirm_token": cf["confirm_token"],
                "question_fa": prop["question_fa"], "proposal": prop,
                "note": "عملیات تا تأیید کاربر متوقف شد — ساخت/تحویلی انجام نشد",
                "finished": datetime.now().isoformat(timespec="seconds"),
            })
            return result

    # 2) build (uses the REAL engine for Android, the R3 pipeline otherwise)
    typ = p["type"]["kind"]
    try:
        if typ == "android":
            from core.engines import android as andmod
            from core.engines.registry import EngineError
            eng = andmod.AndroidEngine()
            if not p["tools"].get("ok"):
                step("tools", False, "ابزار ساخت اندروید موجود نیست")
                return _fail(project, "tools",
                             "پیشنیاز ساخت اندروید آماده نیست: " +
                             ", ".join(p["tools"].get("notes") or []), **result)
            if not p["signing_key_present"]:
                step("signing-key", False, "کلید امضای محصول موجود نیست")
                return _fail(project, "signing-key",
                             "کلید امضای «%s» یافت نشد؛ بدون تغییر هویت امضا نمیتوان APK ساخت. "
                             "(abandon: کلید را با /api/factory/android/key و confirm=true بسازید)"
                             % str(meta.get("package") or "?"), **result)
            slog = andmod.StepLog()
            fmt = str(meta.get("release_format") or "apk").lower()
            kind = "aab" if fmt == "aab" else "apk"
            rep = eng.build(project, {"kind": kind, "sign": True}, slog.add)
            out_dir = Path(rep["output_dir"])
            if kind == "aab":
                apk = Path((rep.get("artifacts") or {}).get("universal_apk", {}).get("path") or "")
                delivered_kind = "aab"
            else:
                apk = Path((rep.get("artifacts") or {}).get("apk", {}).get("path") or "")
                delivered_kind = "apk"
            if not apk.is_file():
                apk = out_dir / "app-release.apk" if kind == "apk" else out_dir / "app-release.aab"
            # Independent verification with the same engine (real apksigner).
            v = eng.verify_artifacts(out_dir, slog.add, expect={
                "package": rep.get("package"),
                "version_code": rep.get("version_code"),
                "version_name": rep.get("version_name")})
            result["build"] = {"status": OK, "method": p["build_method"],
                               "kind": delivered_kind, "output_dir": str(out_dir),
                               "duration_s": rep.get("duration_s"),
                               "version_code": rep.get("version_code"),
                               "package": rep.get("package")}
            result["tests"] = {"status": OK, "method": p["test_method"],
                               "badging": v.get("badging"),
                               "note": "کامپایل واقعی + badging + classes.dex + apksigner"}
            signed = bool(rep.get("sign_verified")) and bool(v.get("sign_verified"))
            result["signature"] = {"status": OK if signed else UNVERIFIED,
                                   "sign_verified": bool(signed),
                                   "note": "امضا با کلید اختصاصی همان محصول — هویت/پکیج تغییر نکرد"}
            step("build", True, "%s v%s (vc=%s)" % (kind, p["version"],
                                                    rep.get("version_code")))
            step("test-artifact", True, "badging + classes.dex + apksigner")
            step("sign", signed, "sign_verified=%s" % signed)
            artifact = apk
        elif typ == "other":
            from core import exporter
            rep = exporter.export(project)
            artifact = Path(rep["path"])
            result["build"] = {"status": OK if rep.get("integrity_bad_file") is None else FAIL,
                               "method": p["build_method"], "kind": "source-zip"}
            step("build", True, "بستهٔ منبع تمیز (نوع نامشخص)")
        else:
            rep = releasemod.build_release(project)
            if not rep.get("ok"):
                step("build", False, "verify_release ناموفق")
                return _fail(project, "build", "ساخت بستهٔ انتشار ناموفق بود", **result)
            artifact = Path(rep["path"])
            result["build"] = {"status": OK, "method": p["build_method"],
                               "kind": "release-zip", "files": rep.get("files"),
                               "technology": rep.get("technology", {}).get("kind")}
            step("build", True, "%s — %d فایل" % (rep["name"], rep.get("files", 0)))
    except Exception as exc:  # noqa: BLE001 — honest failure, never fake green
        log.exception("smart release build failed: %s", project)
        step("build", False, repr(exc)[:220])
        return _fail(project, "build", "ساخت ناموفق: %s" % exc, **result)

    # 3) project tests (non-Android: the combined real gate)
    if typ != "android":
        rep = factory.testrun_combined(project)
        if rep.get("ran") and not rep.get("ok"):
            result["tests"] = {"status": FAIL, "method": p["test_method"],
                               "note": rep.get("note", ""), "detail": rep}
            step("test", False, "آزمونها سرخ شدند — انتشار متوقف")
            return _fail(project, "test", "آزمونها ناموفق بودند", **result)
        if not rep.get("ran"):
            result["tests"] = {"status": UNVERIFIED, "method": p["test_method"],
                               "note": rep.get("note") or "بدون پوشش تست",
                               "detail": rep}
            step("test", False, "بدون پوشش تست — وضعیت تأییدنشده")
        else:
            result["tests"] = {"status": OK, "method": p["test_method"],
                               "note": rep.get("note", "")}
            step("test", True, "آزمونها سبز")

    # 4) artifact validation + safe delivery to Downloads
    v = _validate_artifact(artifact, "android" if typ == "android" else "zip")
    result["artifact_validation"] = v
    step("validate", v["ok"], "، ".join(v.get("checks", {}).keys()))
    if not v["ok"]:
        return _fail(project, "validate", "خروجی سالم نیست: " + ", ".join(v["problems"]), **result)
    deliver = _deliver(artifact, project, p["version"], downloads_dir)
    result["downloads"] = deliver
    step("deliver", deliver["status"] == OK, deliver.get("note", ""))
    result["artifact"] = {
        "source_path": str(artifact), "path": deliver.get("path"),
        "name": deliver.get("name"), "size_bytes": v["size_bytes"],
        "sha256": v["sha256"], "kind": result["build"].get("kind"),
    }
    if deliver["status"] != OK:
        result["status"] = PARTIAL
        result["note"] = "خروجی محلی سالم است ولی ذخیره در Downloads ناموفق شد"
        return _append_report(result)

    # 5) AF-29 ActionGate — git/GitHub is a SENSITIVE operation. It is only
    # attempted when real, verified preconditions exist. A missing artifact,
    # failed test, or AI-Factory-root repository BLOCKS it before any command.
    from core import action_gate as ag, opstatus as ost
    prep = result.get("git_prepare") or {}
    root_risk = any(("ریشهٔ AI Factory" in str(x) or "ریشه AI Factory" in str(x))
                    for x in p["risks"])
    preconditions = [
        ag.evidence(ost.PASS if result["build"]["status"] == OK else ost.FAIL,
                    "build", str(result["build"].get("kind") or "")),
        ag.evidence(ost.PASS if v["ok"] else ost.FAIL, "artifact-validation",
                    ", ".join(v["problems"]) if v["problems"] else "ok"),
        ag.evidence(ost.PASS if deliver["status"] == OK else ost.FAIL,
                    "delivery", deliver.get("note", "")),
        ag.evidence(ost.PASS if result["tests"]["status"] == OK else
                    (ost.FAIL if result["tests"]["status"] == FAIL else ost.NOT_TESTED),
                    "tests", result["tests"].get("note", "")),
        ag.evidence(ost.BLOCKED if root_risk else ost.PASS, "scope",
                    "مخزن ریشهٔ AI Factory" if root_risk else "مخزن پروژه"),
    ]
    result["git_preconditions"] = ost.summary(preconditions)
    if prep.get("status") == SKIP:      # user declined repo creation → no push
        sync = {"status": SKIP, "note": "کاربر اتصال مخزن را تأیید نکرد — ارسال نشد",
                "steps": [], "committed": False, "commit_count": 0}
    else:
        try:
            ag.require(preconditions, "git-sync-preconditions")
            sync = git_sync.commit_and_push(
                project, root=d,
                message="Smart Release %s v%s" % (project, p["version"]),
                push=bool(push))
        except ag.ActionDenied as exc:
            log.warning("git sync blocked by preconditions: %s", exc)
            sync = {"status": FAIL, "note": "پیششرط عملیات حساس تأیید نشد: %s"
                    % exc.summary["status"], "steps": [], "committed": False,
                    "commit_count": 0, "verification": exc.summary}
        except Exception as exc:  # noqa: BLE001 — never fake a sync
            log.exception("git sync failed: %s", project)
            sync = {"status": FAIL, "note": "خطای داخلی همگامسازی: %s" % exc, "steps": []}
    result["git"] = {"status": sync["status"], "committed": sync.get("committed", False),
                     "commit_count": sync.get("commit_count", 0),
                     "files_committed": sync.get("files_committed", 0),
                     "head_local": sync.get("head_local"),
                     "head_before": sync.get("head_before"),
                     "note": sync.get("note", ""),
                     "blocked_files": [b["path"] for b in
                                       (sync.get("preflight") or {}).get("blocked_files", [])]}
    result["github"] = {"status": sync["status"] if sync.get("pushed") else
                        (OK if sync.get("online_match") else sync["status"]),
                        "remote": (sync.get("preflight") or {}).get("remote_info", {}),
                        "visibility": ((sync.get("preflight") or {})
                                       .get("github", {}) or {}).get("visibility"),
                        "remote_head": sync.get("remote_head"),
                        "online_match": sync.get("online_match"),
                        "note": sync.get("note", "")}
    if prep.get("url"):
        result["github"]["url"] = prep["url"]
        result["github"]["repo"] = prep.get("repo")
        result["github"]["created"] = bool(prep.get("created"))
    step("git-commit", sync.get("committed", False),
         "%d ثبت" % sync.get("commit_count", 0))
    step("github-push", sync.get("pushed", False), sync.get("note", "") or "ارسال شد")

    # 6) AF-26 / H14 — overall status is DERIVED FROM EVIDENCE, never from a
    # single "git status ok" flag.  In particular commit_count=0 without a
    # verified remote head is NOT_TESTED, and a root-repository risk is
    # BLOCKED: neither may be reported as a complete success.
    from core import opstatus as ost

    rv = ost.Recorder()

    def vadd(status, name, detail, evidence=None, required=True):
        rv.add(status, name, detail, evidence, required=required)

    vadd(ost.PASS if p["security"]["ok"] else ost.FAIL, "security-gate",
         "اسکن محتوا: %d فایل" % p["security"]["scanned"])

    prep = result.get("git_prepare") or {}
    git_requested = bool(push and auto_git and
                         prep.get("status") not in (SKIP, ""))
    if auto_git:
        if prep.get("status") == OK:
            vadd(ost.PASS, "git-prepare", prep.get("note", ""))
        elif prep.get("status") == SKIP:
            vadd(ost.SKIP, "git-prepare",
                 prep.get("note") or "کاربر اتصال مخزن را تأیید نکرد")
        else:
            vadd(ost.FAIL, "git-prepare", prep.get("note", "") or
                 str(prep.get("status")))
    else:
        vadd(ost.SKIP, "git-prepare", "auto_git=False — درخواست نشد", required=False)

    vadd(ost.PASS if result["build"]["status"] == OK else ost.FAIL,
         "build", str(result["build"].get("kind") or ""))
    ts = result["tests"]["status"]
    vadd(ost.PASS if ts == OK else (ost.FAIL if ts == FAIL else ost.NOT_TESTED),
         "tests", str(result["tests"].get("note", "")))
    sig = result["signature"]["status"]
    if typ == "android":
        vadd(ost.PASS if sig == OK else ost.NOT_TESTED, "signature",
             "sign_verified=%s" % result["signature"].get("sign_verified"))
    else:
        vadd(ost.SKIP, "signature",
             "امضای APK برای بستهٔ وب موضوعیتی ندارد", required=False)
    vadd(ost.PASS if v["ok"] else ost.FAIL, "artifact-validation",
         ", ".join(sorted(v.get("checks", {}))))
    vadd(ost.PASS if deliver["status"] == OK else ost.FAIL, "delivery",
         deliver.get("note", ""))

    if git_requested:
        root_risk = any(("ریشهٔ AI Factory" in str(x) or "ریشه AI Factory" in str(x))
                        for x in p["risks"])
        if root_risk:
            vadd(ost.BLOCKED, "git-sync",
                 "مخزن شناسایشده ریشهٔ AI Factory است — ارسال مجاز نیست")
        elif sync.get("status") == FAIL:
            vadd(ost.FAIL, "git-sync", sync.get("note", ""))
        elif sync.get("pushed") and sync.get("online_match") is True:
            vadd(ost.PASS, "git-sync",
                 "push واقعی + تطابق شناسهٔ آنلاین (commit_count=%d)" %
                 sync.get("commit_count", 0),
                 {"head_local": sync.get("head_local"),
                  "remote_head": sync.get("remote_head")})
        elif sync.get("status") == OK and not push:
            vadd(ost.SKIP, "git-sync", "push طبق درخواست انجام نشد")
        else:
            vadd(ost.NOT_TESTED, "git-sync",
                 "هیچ مدرکی برای commit/push وجود ندارد (commit_count=%d, pushed=%s)"
                 % (sync.get("commit_count", 0), sync.get("pushed")),
                 {"commit_count": sync.get("commit_count", 0),
                  "pushed": sync.get("pushed")})
    else:
        vadd(ost.SKIP, "git-sync",
             "git جزو درخواست نبوده یا توسط کاربر رد شد", required=False)

    verification = rv.finish()
    result["verification"] = verification
    verdict = verification["status"]
    uncertain = [c["name"] for c in verification["checks"]
                 if c["required"] and c["status"] in
                 (ost.SKIP, ost.NOT_TESTED, ost.BLOCKED)]
    failed = [c["name"] for c in verification["checks"]
              if c["required"] and c["status"] == ost.FAIL]

    if verdict == ost.PASS:
        result["status"] = OK
        result["note"] = "انتشار کامل و همگامسازی تأیید شد"
    elif verdict == ost.FAIL:
        result["status"] = FAIL
        result["note"] = "مراحل ناموفق: %s" % ", ".join(failed)
    else:
        result["status"] = PARTIAL
        result["note"] = ("عملیات کامل تأیید نشد ( وضعیت: %s — مراحل بدون مدرک: %s )"
                          % (verdict, ", ".join(uncertain) or "—"))

    result["finished"] = datetime.now().isoformat(timespec="seconds")
    result["status_label"] = LABELS[result["status"]]
    result["verification_status"] = verdict
    result["verification_label"] = verdict
    result["status_label"] = "%s (%s)" % (LABELS[result["status"]], verdict)
    result["test_status_label"] = LABELS[result["tests"]["status"]]
    result["git_status_label"] = LABELS[result["git"]["status"]]
    result["github_status_label"] = LABELS[result["github"]["status"]]
    result["signature_status_label"] = LABELS[result["signature"]["status"]]
    result["summary_fa"] = _summary(result)
    factory.append_history(project, "smart-release",
                           "%s v%s → %s (git=%s, github=%s)"
                           % (result["status_label"], p["version"],
                              deliver.get("path", "—"),
                              result["git_status_label"], result["github_status_label"]))
    return _append_report(result)


def _summary(r: dict) -> str:
    a = r.get("artifact") or {}
    g = r.get("git") or {}
    gh = r.get("github") or {}
    return ("پروژه: %s (%s) | نسخه: %s | وضعیت: %s\n"
            "بررسی مراحل: %s\n"
            "خروجی: %s\n"
            "هش SHA256: %s | حجم: %s بایت\n"
            "ساخت: %s | آزمون: %s | امضا: %s\n"
            "Git: %s (%d ثبت) | GitHub: %s | تطابق آنلاین: %s"
            % (r.get("project"), r.get("project_type"), r.get("version"),
               r.get("status_label"), r.get("verification_status", "NOT TESTED"),
               a.get("path") or "—", a.get("sha256") or "—",
               a.get("size_bytes", 0),
               LABELS.get(r.get("build", {}).get("status", SKIP), "تأییدنشده"),
               r.get("test_status_label", "تأییدنشده"),
               r.get("signature_status_label", "تأییدنشده"),
               r.get("git_status_label", "انجامنشده"), g.get("commit_count", 0),
               r.get("github_status_label", "انجامنشده"),
               "بله" if gh.get("online_match") else "خیر/تأییدنشده"))
