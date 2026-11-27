"""AF-15 — کدِ کیفیت محصول: ارزیابی واقعی، بدون امتیاز ساختگی.

Layered on the EXISTING tools (never re-implements them):
  * core.checker        → syntax (py ast / js node --check / json / html)
  * core.typetests      → per-type REAL behavioural contract (boots server)
  * core.runner         → project's own test suite
  * core.security_gate  → reused conceptually: warnings are SEPARATE from
                          definite errors (same honest philosophy)

Quality additions (confident detection ONLY — no noisy heuristics):
  * python: local import of a module that does not exist in the project
    (relative imports and same-directory siblings) → definite error
  * js: require("./…") of a file that does not exist → definite error
  * warnings (probable, clearly separated): bare `except: pass`,
    eval(/exec(, subprocess … shell=True, غیرفعالکردن تأیید TLS,
    pickle.loads, TODO/FIXME left in code

Final status (no fake scores/percents):
  * FAIL          → at least one definite error, or a test/contract that
                    RAN and failed
  * NEEDS_REVIEW  → warnings only, or no test coverage (honest unknown)
  * PASS          → zero definite errors AND every test that ran is green
"""

import ast
import json
import re
import sys
from pathlib import Path

import core.config as config
from core import checker, paths

log = __import__("logging").getLogger("ai-factory.quality")

_TEXT_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".json", ".html", ".css",
                  ".md", ".txt", ".xml", ".yaml", ".yml", ".sh", ".bat"}

# ------------------------------------------------------- definite errors ----

_IMPORT_RX = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.,\s]+))")

_WARN_PATTERNS = [
    ("warn-bare-except-pass",
     re.compile(r"except\s*(?:Exception)?\s*:\s*(?:\n?\s*)?pass")),
    ("warn-eval-exec", re.compile(r"\b(?:eval|exec)\s*\(")),
    ("warn-shell-true", re.compile(r"shell\s*=\s*True")),
    ("warn-ssl-verify-false", re.compile(r"verify\s*=\s*False")),
    ("warn-pickle-loads", re.compile(r"pickle\.loads?\s*\(")),
    ("warn-todo-left", re.compile(r"\b(?:TODO|FIXME|XXX)\b")),
]


def _iter_files(d: Path):
    skip = config.EXPORT_EXCLUDE_NAMES
    for f in sorted(d.rglob("*")):
        if not f.is_file():
            continue
        if set(f.parts) & skip or f.suffix.lower() == ".pyc":
            continue
        yield f


def _py_local_imports(d: Path, f: Path, rel: str, errors: list) -> None:
    """Definite: RELATIVE import of a local module/package that does not
    exist. Absolute imports are never flagged (stdlib/external is not
    resolvable without an install step — not confident ⇒ not reported)."""
    try:
        text = f.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=rel)
    except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
        return                      # syntax already reported by checker
    pkg_dir = f.parent
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.level:
            continue
        base = pkg_dir
        for _ in range(node.level - 1):
            base = base.parent
        if node.module:
            cand = (base / node.module.replace(".", "/"))
            if not (cand.with_suffix(".py").is_file()
                    or (cand / "__init__.py").is_file()):
                errors.append({"file": rel, "kind": "import-missing",
                               "line": node.lineno,
                               "message": "ماژول محلی «%s» یافت نشد "
                                          "(import نسبی)" % node.module})
        else:                       # from . import x
            for alias in node.names:
                cand = base / alias.name.replace(".", "/")
                if not (cand.with_suffix(".py").is_file()
                        or (cand / "__init__.py").is_file()):
                    errors.append({"file": rel, "kind": "import-missing",
                                   "line": node.lineno,
                                   "message": "نماد محلی «%s» در بستهٔ جاری "
                                              "یافت نشد (import نسبی)"
                                              % alias.name})


def _js_local_requires(d: Path, f: Path, rel: str, errors: list) -> None:
    try:
        text = f.read_text(encoding="utf-8")
    except OSError:
        return
    for m in re.finditer(
            r"require\s*\(\s*['\"](\.[^'\"]+)['\"]\s*\)", text):
        spec = m.group(1)
        base = (f.parent / spec).resolve()
        d_res = d.resolve()
        if d_res not in base.parents and base != d_res:
            continue            # escapes project — path guard's domain
        if base.is_file():
            continue
        for cand in (base.with_suffix(".js"),
                     base / "index.js",
                     base / "package.json"):
            if cand.is_file():
                break
        else:
            line = text[:m.start()].count("\n") + 1
            errors.append({"file": rel, "kind": "require-missing",
                           "line": line,
                           "message": "فایل محلی «%s» برای require یافت نشد"
                                      % spec})


def _warnings(d: Path) -> list[dict]:
    out: list[dict] = []
    for f in _iter_files(d):
        rel = f.relative_to(d).as_posix()
        if f.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if f.stat().st_size > 1_000_000:
            continue
        for kind, rx in _WARN_PATTERNS:
            for m in rx.finditer(text):
                out.append({"file": rel, "kind": kind,
                            "line": text[:m.start()].count("\n") + 1,
                            "preview": m.group(0)[:60]})
    return out


def evaluate(project: str, *, boot: bool = True) -> dict:
    """Full quality evaluation of an isolated project. `boot=False` skips
    the REAL contract/test run (static only) — the flag is honest in the
    report (tests_ran=False)."""
    d = paths.project_dir(project)              # name validated here
    static = checker.check(d)
    errors = list(static["errors"])

    # definite local-reference errors (confident only)
    for f in _iter_files(d):
        rel = f.relative_to(d).as_posix()
        if f.suffix.lower() == ".py":
            _py_local_imports(d, f, rel, errors)
        elif f.suffix.lower() in (".js", ".mjs", ".cjs"):
            _js_local_requires(d, f, rel, errors)

    warnings = _warnings(d)

    own = {"ran": False, "ok": False, "note": "اجرا نشده (static-only)"}
    contract = {"ran": False, "ok": False, "errors": [],
                "note": "اجرا نشده (static-only)"}
    verification_error = False
    if boot:
        from core import runner, typetests
        try:
            own = runner.run_tests(project)
        except Exception as exc:             # noqa: BLE001 — honest record
            verification_error = True
            own = {"ran": False, "ok": False, "note": f"خطا: {exc}"}
        try:
            contract = typetests.run(project)
        except Exception as exc:             # noqa: BLE001 — honest record
            verification_error = True
            contract = {"ran": False, "ok": False, "errors": [],
                        "note": f"تست قرارداد اجرا نشد: {exc}"}

    tests_ran = bool(own.get("ran") or contract.get("ran"))
    tests_ok = (not own.get("ran") or own.get("ok")) and \
               (not contract.get("ran") or contract.get("ok"))

    if verification_error or errors or (own.get("ran") and not own.get("ok")) \
            or (contract.get("ran") and not contract.get("ok")):
        status = "FAIL"
    elif warnings or not tests_ran:
        status = "NEEDS_REVIEW"
    else:
        status = "PASS"

    return {
        "project": project,
        "status": status,
        "verification_error": verification_error,
        "errors": errors,
        "warnings": warnings,
        "tests": {"own": {k: own.get(k) for k in ("ran", "ok", "exit", "note")},
                  "contract": {k: contract.get(k)
                               for k in ("ran", "ok", "no_coverage", "note")},
                  "ran": tests_ran, "all_ok": tests_ok},
        "static": {"checked": static.get("checked"),
                   "not_checked_kinds": static.get("not_checked_kinds")},
        "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
    }


if __name__ == "__main__":  # pragma: nocover — manual debug
    print(json.dumps(evaluate(sys.argv[1]), ensure_ascii=False, indent=2))
