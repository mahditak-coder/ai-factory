"""R3 — release packager: clean, standalone, runnable release ZIPs.

The plain exporter (core/exporter.py) ships the project as-is (minus junk/
secrets). A RELEASE package is stricter and self-contained:

  * factory-internal files are removed (meta.json, tests/, test_*.py)
  * runtime data is removed (*.db / *.sqlite* — recreated at boot by products
    that call init_db(); keeps user hashes out of distribution)
  * technology + dependencies are DETECTED from the real sources (AST import
    scan minus stdlib/local modules, requirements.txt, package.json)
  * release.json records product version, project type, dependencies, env
    config and the exact file manifest
  * a type-tailored README.md is generated (original product README is merged
    underneath, never lost)
  * run.sh (POSIX) and run.bat (Windows) launchers match the technology
  * the ZIP is verified structurally (verify_release) before being reported

Hosting model per kind:
  static-web      → drop the folder on ANY static host (index.html entry)
  python-server   → `python3 server.py`, port from env (PORT / AI_FACTORY_PORT)
  node-server     → `node <entry>` (detector supported; untested — no node
                    product exists in this environment yet)
"""

import ast
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import core.config as config
from core import paths

log = __import__("logging").getLogger("ai-factory.release")

RELEASE_VERSION = "1.0.0"        # پیش‌فرض وقتی پروژه هنوز نسخه ندارد

# AF-12 — نسخه‌گذاری مستقل هر پروژه (semver سه‌جزئی، قواعد صریح)
#
#  * منبعِ نسخه: فیلد «version» در meta.json خود پروژه.
#  * نسخهٔ اولیه: پروژه‌ای که فیلد ندارد → 1.0.0 (خوانده می‌شود، بدون نوشتن).
#  * افزایش نسخه: فقط صریح، با set_version()/API — هیچ افزایش خودکاری وجود
#    ندارد؛ ساخت، بسته‌بندی یا شکستِ ساخت هرگز نسخه را تغییر نمی‌دهد.
#  * فرمت مجاز: semver سخت‌گیرانه (بدون صفر پیشین) — نسخهٔ بد رد می‌شود، حدس زده نمی‌شود.
VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def project_version(d: Path) -> str:
    """نسخهٔ مؤثر پروژه: meta.json.version (اعتبارسنجی‌شده) یا 1.0.0 پیش‌فرض."""
    d = Path(d)
    try:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    v = meta.get("version")
    if v is None or v == "":
        return RELEASE_VERSION
    v = str(v).strip()
    if not VERSION_RE.match(v):
        raise ValueError(
            "نسخهٔ پروژه نامعتبر است: %r (فرمت لازم: MAJOR.MINOR.PATCH) — "
            "با set_version اصلاح کنید" % v)
    return v


def set_version(project: str, version: str) -> dict:
    """تنظیم صریح نسخهٔ پروژه (تنها راه تغییر نسخه — بدون هیچ افزایش خودکار)."""
    version = str(version).strip()
    if not VERSION_RE.match(version):
        raise ValueError("نسخه نامعتبر: %r — فرمت لازم MAJOR.MINOR.PATCH "
                         "(مثال: 1.2.0)" % version)
    d = paths.project_dir(project)
    mp = d / "meta.json"
    # AF-12: پروژهٔ خالص اندروید ممکن است meta.json نداشته باشد —
    # نخستین تنظیم صریح نسخه، آن را با نام+نسخه ایجاد می‌کند.
    if mp.is_file():
        meta = json.loads(mp.read_text(encoding="utf-8"))
    else:
        meta = {"name": project}
    old = str(meta.get("version") or RELEASE_VERSION)   # فقط برای تاریخچه؛
    # حتی اگر نسخهٔ فعلی دستی خراب باشد، تنظیمِ نسخهٔ درست باید ممکن باشد
    if old == version:
        return {"project": project, "version": version, "changed": False}
    meta["version"] = version
    meta["modified"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = mp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(mp)
    # AF-13 — سازگاری نسخه در تمام اجزای بسته: package.json پروژهٔ node هم
    # هم‌زمان به‌روزرسانی می‌شود تا با meta.json/release.json یکسان بماند.
    pkgp = d / "package.json"
    if pkgp.is_file():
        try:
            pkg = json.loads(pkgp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pkg = None
        if isinstance(pkg, dict):
            if pkg.get("version") != version:
                pkg["version"] = version
                ptmp = pkgp.with_suffix(".json.tmp")
                ptmp.write_text(json.dumps(pkg, ensure_ascii=False, indent=1)
                                + "\n", encoding="utf-8")
                ptmp.replace(pkgp)
    from core import factory
    factory.append_history(project, "version-set",
                           "%s → %s" % (old, version))
    return {"project": project, "version": version, "previous": old,
            "changed": True}

STATIC_TYPES = {"webpage", "webtool", "webapp", "dashboard", "shop", "pwa"}
PY_TYPES = {"api", "ai-tool", "fullstack", "fullstack-python"}

# Factory-internal / dev-time files that must never ship in a release
RELEASE_NAME_EXCLUDES = {"meta.json"}              # factory registry, not app source
# AF-24: both spellings are dev-time test suites — neither may ship. A real
# teacher-android bundle had test/cloud_server.test.js (with a test password
# literal) leaking into releases because only the plural form was excluded.
RELEASE_DIR_EXCLUDES = {"tests", "test"}           # unittest suites are dev-time
RELEASE_TEST_FILE_RE = re.compile(r"(^|/)test_[^/]*\.py$")
RELEASE_FORBIDDEN_RE = re.compile(
    r"(^|/)(meta\.json|\.env|opencode\.json|server\.out|\.factory)$"
    r"|__pycache__/|\.pyc$|\.db($|-wal$|-shm$)|\.sqlite3?$"
    r"|\.token$|\.pem$|\.key$|(^|/)tests/")


# ── technology / dependency detection ────────────────────────────────────────

def _py_env_names(d: Path) -> list:
    """Env-var configuration names actually read by the product's sources."""
    names = set()
    pat = re.compile(
        r"""os\.environ\.get\(\s*["']([A-Z][A-Z0-9_]+)["']"""
        r"""|os\.environ\[\s*["']([A-Z][A-Z0-9_]+)["']\s*\]"""
        r"""|os\.getenv\(\s*["']([A-Z][A-Z0-9_]+)["']""")
    for f in d.rglob("*.py"):
        try:
            for m in pat.finditer(f.read_text(encoding="utf-8", errors="replace")):
                names.add(next(g for g in m.groups() if g))
        except OSError:
            continue
    return sorted(names)


def _py_deps(d: Path) -> list:
    """Third-party top-level imports (AST) minus stdlib and local modules."""
    stdlib = getattr(sys, "stdlib_module_names", frozenset())
    local = {p.stem for p in d.rglob("*.py")} | {"server", "main", "app"}
    third = set()
    for f in d.rglob("*.py"):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    top = a.name.split(".")[0]
                    if top not in stdlib and top not in local:
                        third.add(top)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                top = node.module.split(".")[0]
                if top not in stdlib and top not in local:
                    third.add(top)
    return sorted(third)


def _requirements_deps(d: Path) -> list:
    req = d / "requirements.txt"
    if not req.is_file():
        return []
    deps = []
    for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "//")):
            continue
        name = re.split(r"[<>=!~\[;\s]", line, 1)[0].strip()
        if name and name.isidentifier():
            deps.append(name)
    return sorted(set(deps))


def _node_deps(d: Path) -> list:
    pkg = d / "package.json"
    if not pkg.is_file():
        return []
    try:
        return sorted((json.loads(pkg.read_text(encoding="utf-8"))
                       .get("dependencies") or {}).keys())
    except (OSError, ValueError):
        return []


def _entry_for(d: Path, type_id: str) -> str | None:
    if type_id in STATIC_TYPES:
        return "index.html" if (d / "index.html").is_file() else None
    # AF-13 — node projects: package.json "main" is the real entry
    pkg = d / "package.json"
    if pkg.is_file():
        try:
            main = (json.loads(pkg.read_text(encoding="utf-8"))
                    .get("main") or "").strip()
        except (OSError, ValueError):
            main = ""
        if main and (d / main).is_file():
            return main
    for cand in ("server.js", "server.py", "main.py", "app.py"):
        if (d / cand).is_file():
            return cand
    return None


def detect_technology(d: Path, type_id: str) -> dict:
    """Detect kind, runtime, entry, dependencies and env config from real files."""
    entry = _entry_for(d, type_id)
    if (d / "package.json").is_file() and not (d / "server.py").is_file():
        return {"kind": "node-server", "hosting": "standalone",
                "runtime": "Node.js 18+", "entry": entry or "index.js",
                "deps": _node_deps(d), "deps_spec": _node_specs(d),
                "env": _py_env_names(d) or ["PORT"]}
    if type_id in PY_TYPES:
        deps = sorted(set(_py_deps(d)) | set(_requirements_deps(d)))
        return {"kind": "python-server", "hosting": "standalone",
                "runtime": "Python 3.8+", "entry": entry, "deps": deps,
                "deps_spec": _requirements_specs(d),
                "env": _py_env_names(d) or ["PORT"]}
    tech = {"kind": "static-web", "hosting": "static", "runtime": None,
            "entry": entry, "deps": [], "deps_spec": [], "env": []}
    if type_id == "pwa":
        tech["pwa"] = {"manifest": (d / "manifest.json").is_file(),
                       "service_worker": (d / "sw.js").is_file()}
    return tech


# ── release file generation ──────────────────────────────────────────────────

def _run_sh(tech: dict) -> str:
    if tech["kind"] == "static-web":
        return ("#!/bin/sh\n"
                "# اجرای محلی محصول استاتیک (به AI Factory وابسته نیست)\n"
                "cd \"$(dirname \"$0\")\"\n"
                "PORT=\"${PORT:-8080}\"\n"
                "echo \"serving on http://127.0.0.1:$PORT\"\n"
                "exec python3 -m http.server \"$PORT\" --bind 127.0.0.1\n")
    if tech["kind"] == "node-server":
        # AF-13 — واقعی: اجرا با node، نه python3
        return ("#!/bin/sh\n"
                "# اجرای سرور مستقل محصول (به AI Factory وابسته نیست)\n"
                "cd \"$(dirname \"$0\")\"\n"
                "PORT=\"${PORT:-8080}\"\n"
                "export PORT\n"
                "exec node " + (tech.get("entry") or "server.js") + "\n")
    return ("#!/bin/sh\n"
            "# اجرای سرور مستقل محصول (به AI Factory وابسته نیست)\n"
            "cd \"$(dirname \"$0\")\"\n"
            "PORT=\"${PORT:-8080}\"\n"
            "export PORT\n"
            "export AI_FACTORY_PORT=\"${AI_FACTORY_PORT:-$PORT}\"\n"
            "exec python3 \"$0.run-entry\"\n").replace("$0.run-entry",
                                                      tech.get("entry") or "server.py")


def _run_bat(tech: dict) -> str:
    if tech["kind"] == "static-web":
        return ("@echo off\r\n"
                "rem Local run for a static product (no AI Factory needed)\r\n"
                "cd /d \"%~dp0\"\r\n"
                "if \"%PORT%\"==\"\" set PORT=8080\r\n"
                "py -m http.server %PORT% --bind 127.0.0.1 || python -m http.server %PORT% --bind 127.0.0.1\r\n")
    if tech["kind"] == "node-server":
        # AF-13 — واقعی: اجرا با node، نه python3
        entry = tech.get("entry") or "server.js"
        return ("@echo off\r\n"
                "rem Standalone server run (no AI Factory needed)\r\n"
                "cd /d \"%~dp0\"\r\n"
                "if \"%PORT%\"==\"\" set PORT=8080\r\n"
                "set AI_FACTORY_PORT=%PORT%\r\n"
                "node " + entry + "\r\n")
    entry = tech.get("entry") or "server.py"
    return ("@echo off\r\n"
            "rem Standalone server run (no AI Factory needed)\r\n"
            "cd /d \"%~dp0\"\r\n"
            "if \"%PORT%\"==\"\" set PORT=8080\r\n"
            "set AI_FACTORY_PORT=%PORT%\r\n"
            "py " + entry + " || python " + entry + "\r\n")


def _readme_text(project, type_id, tech, rmeta, original_readme, panel_label) -> str:
    deps = tech["deps"]
    deps_line = "فقط کتابخانهٔ استاندارد (بدون وابستگی خارجی)" if not deps \
        else "، ".join(deps) + "  (نصب: pip install " + " ".join(deps) + ")"
    if tech["kind"] == "static-web":
        quick = ("این محصول وب استاتیک است؛ برای میزبانی کافی است کل پوشه روی هر\n"
                 "هاست استاتیک قرار بگیرد (nginx، GitHub Pages، هر CDN…).\n\n"
                 "اجرای محلی:\n\n    sh run.sh          # سپس http://127.0.0.1:8080\n\n"
                 "یا فایل index.html را مستقیم در مرورگر باز کنید.")
        deploy = ("استقرار: پوشه را عیناً روی هاست استاتیک آپلود کنید؛ نقطهٔ ورود "
                  "index.html است و همهٔ ارجاع‌ها نسبی‌اند.")
    elif tech["kind"] == "node-server":
        quick = ("    npm install" + ("\n    node " + tech["entry"] if tech.get("entry") else "") +
                 "\n\nسپس http://127.0.0.1:8080 (یا ش run.sh را اجرا کنید)")
        deploy = "استقرار: Node.js 18+ روی سرور؛ اجرای node " + str(tech.get("entry")) + " با PORT دلخواه."
    else:
        quick = ("    python3 server.py        # یا:  sh run.sh\n"
                 "پورت از env خوانده می‌شود؛ نمونه:\n\n"
                 "    PORT=8080 python3 server.py\n\n"
                 "سپس http://127.0.0.1:8080 را در مرورگر باز کنید.")
        deploy = ("استقرار: فقط Python 3.8+ لازم است (بدون وابستگی خارجی اگر بخش "
                  "وابستگی‌ها خالی باشد). روی سرور با systemd/ supervisord یا "
                  "termux-boot سرویس کنید؛ پورت را با env انتخاب کنید.")
    lines = [
        "# " + project,
        "",
        (rmeta["description"] or "بدون توضیح"),
        "",
        "| مشخصه | مقدار |",
        "|---|---|",
        "| نوع پروژه | %s (`%s`) |" % (panel_label, type_id),
        "| نسخهٔ انتشار | %s (build %s) |" % (rmeta["version"], rmeta["build"]),
        "| فناوری | %s — entry: `%s` |" % (tech["kind"], tech.get("entry") or "—"),
        "| وابستگی‌ها | %s |" % deps_line,
        "",
        "## اجرای سریع",
        "",
        quick,
        "",
        "## استقرار",
        "",
        deploy,
        "",
        "## پیکربندی (متغیرهای محیطی)",
        "",
    ]
    env = tech.get("env") or []
    if env:
        for e in env:
            lines.append("- `%s`" % e)
    else:
        lines.append("این محصول متغیر محیطی خاصی نمی‌خواند.")
    lines += [
        "",
        "مشخصات کامل انتشار: `release.json` — فهرست فایل‌ها، نسخه، نوع، وابستگی‌ها.",
        "",
    ]
    if original_readme:
        lines += ["---", "", "# مستندات محصول (تولیدشده توسط سازنده)", "",
                  original_readme.rstrip(), ""]
    lines += ["---", "", "بستهٔ انتشار مستقل — ساخته‌شده با خط لولهٔ R3 کارخانهٔ AI Factory؛ "
              "این بسته به کارخانه وابسته نیست.", ""]
    return "\n".join(lines)


# ── packaging ────────────────────────────────────────────────────────────────

def _release_allowed(f: Path, project_dir: Path, rel: str) -> bool:
    from core import exporter
    if not exporter._allowed(f, project_dir):
        return False
    if f.name in RELEASE_NAME_EXCLUDES:
        return False
    if RELEASE_DIR_EXCLUDES.intersection(Path(rel).parts):
        return False
    if RELEASE_TEST_FILE_RE.search(rel):
        return False
    return True


def _requirements_specs(d: Path) -> list:
    """وابستگی‌های پایتون با نسخهٔ ثبت‌شده (خط requirements.txt عیناً)."""
    req = d / "requirements.txt"
    if not req.is_file():
        return []
    specs = []
    for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "//")):
            continue
        if re.match(r"^[A-Za-z0-9_.\-]+([<>=!~].*)?$", line):
            specs.append(line)
    return sorted(set(specs))


def _node_specs(d: Path) -> list:
    """وابستگی‌های Node با نسخه (name@spec از package.json)."""
    pkg = d / "package.json"
    if not pkg.is_file():
        return []
    try:
        deps = json.loads(pkg.read_text(encoding="utf-8")).get("dependencies") or {}
    except (OSError, ValueError):
        return []
    return sorted("%s@%s" % (k, v) for k, v in deps.items())


# quality — اعلام صادقانهٔ وضعیت آزمون‌ها در هر بسته (P11: آزمون‌شده/نشده)
_QUALITY_UNTESTED = {
    "webpage": ["رفتار JS درون مرورگر (تعامل کاربر) — نیازمند مرورگر"],
    "webapp": ["رفتار JS درون مرورگر (تعامل کاربر) — نیازمند مرورگر"],
    "webtool": ["رفتار JS درون مرورگر (تعامل کاربر) — نیازمند مرورگر"],
    "dashboard": ["رفتار JS درون مرورگر (نمودار زنده) — نیازمند مرورگر"],
    "shop": ["رفتار JS درون مرورگر (سبد خرید) — نیازمند مرورگر"],
    "pwa": ["رفتار JS درون مرورگر", "نصب‌پذیری و اجرای آفلاین در مرورگر واقعی"],
    "api": ["بار بالا/تزریق خارج از آزمون‌های کارخانه"],
    "ai-tool": ["عملکرد مدل واقعی — نیازمند API واقعی"],
    "fullstack": ["بار بالا/تزریق خارج از آزمون‌های کارخانه"],
    "fullstack-python": ["بار بالا/تزریق خارج از آزمون‌های کارخانه"],
}


def _quality_block(type_id: str) -> dict:
    untested = list(_QUALITY_UNTESTED.get(type_id, []))
    if type_id in STATIC_TYPES:
        cover = "قرارداد ساختاری (typetests) روی بوت واقعی — در چرخهٔ کارخانه"
    else:
        cover = ("آزمون‌های خود محصول (unittest) + قرارداد رفتاری (typetests) "
                 "روی بوت واقعی — در چرخهٔ کارخانه")
    return {
        "tested_at_packaging": False,
        "covered_by_factory_cycle": cover,
        "untested": untested,
        "how_to_test": ("POST /api/factory/testrun {\"project\": …} — "
                        "گیت واقعی P6-1 (testrun_combined)"),
        "note": ("بسته‌بندی پروژه را تغییر نمی‌دهد؛ آزمون رفتاری از طریق "
                 "چرخهٔ کارخانه (orchestrator/testrun) اجرا می‌شود"),
    }


def build_release(project: str) -> dict:
    d = paths.project_dir(project)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    type_id = meta.get("type", "webpage")
    panel_label = config.PROJECT_TYPE_SPECS.get(type_id, {}).get(
        "panel_label") or type_id
    tech = detect_technology(d, type_id)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    stage = Path(tempfile.mkdtemp(prefix="r3rel-"))
    version = project_version(d)      # AF-12 — نسخهٔ مستقل پروژه (اعتبارسنجی‌شده)
    try:
        files = []
        for f in sorted(d.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(d).as_posix()
            if not _release_allowed(f, d, rel):
                continue
            dest = stage / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dest)
            files.append(rel)
        orig_readme = None
        rp = stage / "README.md"
        if rp.is_file():
            orig_readme = rp.read_text(encoding="utf-8", errors="replace")
            rp.unlink()
        rmeta = {
            "name": project, "type": type_id, "version": version,
            "build": stamp, "description": meta.get("description", ""),
            "source_modified": meta.get("modified", ""),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "generator": "AI-Factory R3 release pipeline",
            "technology": tech,
            "run": {"command": ("sh run.sh" if tech["kind"] == "static-web"
                                else ("node %s" % tech["entry"]
                                      if tech["kind"] == "node-server"
                                      else ("python3 %s" % tech["entry"] if tech.get("entry")
                                            else "—"))),
                    "port_env": tech.get("env") or []},
            "files": files,
            "file_count": len(files),
            "quality": _quality_block(type_id),
        }
        (stage / "release.json").write_text(
            json.dumps(rmeta, ensure_ascii=False, indent=1), encoding="utf-8")
        (stage / "README.md").write_text(
            _readme_text(project, type_id, tech, rmeta, orig_readme, panel_label),
            encoding="utf-8")
        (stage / "run.sh").write_text(_run_sh(tech), encoding="utf-8")
        os.chmod(stage / "run.sh", 0o755)
        (stage / "run.bat").write_text(_run_bat(tech), encoding="utf-8")
        # P11 — manifest SHA256 هر فایل بسته (پس از همه؛ خودش هش نمی‌شود)
        manifest_lines = []
        for f in sorted(stage.rglob("*")):
            if f.is_file():
                rel = f.relative_to(stage).as_posix()
                h = hashlib.sha256(f.read_bytes()).hexdigest()
                manifest_lines.append("%s  %s" % (h, rel))
        (stage / "MANIFEST.sha256").write_text(
            "\n".join(manifest_lines) + "\n", encoding="utf-8")

        out = config.EXPORTS_DIR / ("%s-release-%s.zip" % (project, stamp))
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(stage.rglob("*")):
                if f.is_file():
                    zf.write(f, arcname="%s/%s" % (project,
                                                   f.relative_to(stage).as_posix()))
        bad = None
        with zipfile.ZipFile(out) as zf:
            bad = zf.testzip()
            zipped = len(zf.namelist())
        verify = verify_release(out, project)
        # AF-12 — نسخه از پروژه می‌آید (هرگز خودکار افزایش نمی‌یابد)
        version = project_version(d)
        zip_sha = hashlib.sha256(out.read_bytes()).hexdigest()
        res = {"ok": bool(verify["ok"] and bad is None), "path": str(out),
               "name": out.name, "files": zipped,
               "size_bytes": out.stat().st_size, "integrity_bad_file": bad,
               "version": version, "build": stamp,
               "sha256": zip_sha, "technology": tech, "verify": verify}
        # سوابق انتشار — append-only در همان history.json پروژه (بدون ساختار موازی)
        from core import factory
        factory.append_history(project, "release-build",
                               "v%s build=%s files=%d sha256=%s" % (
                                   version, stamp, zipped, zip_sha[:16]))
        log.info("release built: %s (%d files, ok=%s)", out.name, zipped, res["ok"])
        return res
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def verify_release(zip_path: Path, project: str) -> dict:
    """Full structural verification of a release ZIP (no Factory code inside)."""
    checks, problems = {}, []
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        names = zf.namelist()
        rel_names = []
        top = project + "/"
        for n in names:
            if not n.startswith(top):
                problems.append("نام غیرمنتظرهٔ بسته: " + n)
                continue
            rel_names.append(n[len(top):])
        def has(p):
            return p in rel_names and \
                zf.getinfo(top + p).file_size > 0
        checks["integrity"] = bad is None
        checks["single_root_folder"] = not problems
        forbidden = [n for n in rel_names if RELEASE_FORBIDDEN_RE.search(n)]
        checks["no_factory_or_secret_files"] = not forbidden
        if forbidden:
            problems.append("فایل ممنوع داخل بسته: " + ", ".join(forbidden[:6]))
        checks["release_json"] = has("release.json")
        rmeta = {}
        if checks["release_json"]:
            try:
                rmeta = json.loads(zf.read(top + "release.json").decode("utf-8"))
                for k in ("name", "type", "version", "technology"):
                    if k not in rmeta:
                        problems.append("release.json فاقد کلید: " + k)
                if rmeta.get("name") != project:
                    problems.append("release.json name ناهم‌خوان")
                if rmeta.get("type") not in config.PROJECT_TYPE_SPECS:
                    problems.append("release.json type نامعتبر: %r"
                                    % rmeta.get("type"))
                if not isinstance((rmeta.get("technology") or {}).get("deps"), list):
                    problems.append("release.json technology.deps لیست نیست")
            except ValueError as exc:
                problems.append("release.json خراب: %s" % exc)
        checks["readme"] = has("README.md")
        checks["run_sh"] = has("run.sh")
        checks["run_bat"] = has("run.bat")
        tech = rmeta.get("technology") or {}
        entry = tech.get("entry")
        checks["entry_present"] = bool(entry and has(entry))
        if tech.get("kind") == "static-web":
            checks["index_html"] = has("index.html")
        if tech.get("kind") == "python-server":
            checks["server_py"] = has("server.py") or has("main.py")
        checks["files_present"] = len(rel_names) > 2

    # P11 — manifest SHA256: هر فایل بسته با هش ثبت‌شده مقایسه می‌شود؛
    # فایل تغییر/حذف/اضافه = خرابی بسته. بسته‌های قدیمی (پیش از P11) بدون
    # manifest ساختاری سالم محسوب می‌شوند (سازگاری عقب) و در نتیجه ثبت می‌شود.
    manifest = {}
    manifest_present = (top + "MANIFEST.sha256") in names
    mproblems = []
    if manifest_present:
        try:
            with zipfile.ZipFile(zip_path) as zfm:
                for line in zfm.read(top + "MANIFEST.sha256").decode(
                        "utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    h, _, fn = line.partition("  ")
                    if len(h) != 64 or not fn:
                        mproblems.append("manifest-line-bad: " + line[:60])
                        continue
                    manifest[fn] = h.lower()
                if manifest:
                    zset = set(rel_names) - {"MANIFEST.sha256"}
                    for fn, h in sorted(manifest.items()):
                        if fn not in zset:
                            mproblems.append("manifest-file-missing: " + fn)
                            continue
                        if hashlib.sha256(zfm.read(top + fn)).hexdigest() != h:
                            mproblems.append("manifest-hash-mismatch: " + fn)
                    for extra in sorted(zset - set(manifest)):
                        mproblems.append("manifest-extra-file: " + extra)
        except (ValueError, OSError) as exc:
            mproblems.append("manifest-unreadable: %s" % exc)
        checks["release_manifest"] = bool(manifest) and not mproblems
    return {"ok": not problems and all(checks.values()),
            "checks": checks, "problems": problems,
            "manifest": {"present": manifest_present,
                         "entries": len(manifest),
                         "problems": mproblems}}
