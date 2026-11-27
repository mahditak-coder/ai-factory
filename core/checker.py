"""Factory checker — real static analysis of project files (no fakery).

Supported, honest checks:
  * Python files  → ast.parse (full syntax)
  * JS files      → `node --check` when Node.js is available
  * JSON files    → json.loads
  * HTML files    → minimal sanity (non-empty, has <html> and </html>, UTF-8)
Anything not listed is reported as "not checked" rather than silently passing.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

import core.config as config

log = __import__("logging").getLogger("ai-factory.checker")

_node = None


def _node_bin() -> str | None:
    global _node
    if _node is None:
        from shutil import which
        _node = which("node") or ""
    return _node or None


def _iter_project_files(project_dir: Path):
    skip = config.EXPORT_EXCLUDE_NAMES
    for f in sorted(project_dir.rglob("*")):
        if not f.is_file():
            continue
        parts = set(f.parts)
        if parts & skip:
            continue
        if f.suffix.lower() in (".pyc",):
            continue
        yield f


def check(project_dir: Path) -> dict:
    """Run every applicable real check; return structured results."""
    errors: list[dict] = []
    not_checked: list[str] = []
    checked = {"py": 0, "js": 0, "json": 0, "html": 0}
    node = _node_bin()

    for f in _iter_project_files(project_dir):
        rel = f.relative_to(project_dir).as_posix()
        suffix = f.suffix.lower()
        try:
            if suffix == ".py":
                checked["py"] += 1
                _check_python(f, rel, errors)
            elif suffix in (".js", ".mjs", ".cjs"):
                checked["js"] += 1
                _check_js(f, rel, errors, node if node else None, not_checked)
            elif suffix == ".json":
                checked["json"] += 1
                _check_json(f, rel, errors)
            elif suffix == ".html":
                checked["html"] += 1
                _check_html(f, rel, errors)
            else:
                not_checked.append(rel)
        except Exception as exc:  # noqa: BLE001
            log.error("checker internal error on %s: %s", rel, exc)
            errors.append({"file": rel, "kind": "internal",
                           "message": f"خطای داخلی بررسی‌کننده: {exc}"})
    return {
        "ok": not errors,
        "errors": errors,
        "checked": checked,
        "paths_excluded": [".factory", "__pycache__"],
        "not_checked_kinds": sorted({Path(x).suffix for x in not_checked}) if not_checked else [],
    }


def _check_python(path: Path, rel: str, errors: list) -> None:
    if path.stat().st_size == 0:
        return
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
        line = getattr(exc, "lineno", None)
        found = getattr(exc, "msg", "")
        found = found if isinstance(found, str) and found else str(exc)
        errors.append({"file": rel, "kind": "python-syntax",
                       "line": line, "message": found})
    except OSError as exc:
        errors.append({"file": rel, "kind": "python-read", "message": str(exc)})


def _check_js(path: Path, rel: str, errors: list, node: str | None, nc: list) -> None:
    if node is None:
        nc.append(rel)
        return
    if path.stat().st_size == 0:
        return
    try:
        proc = subprocess.run(
            [node, "--check", str(path)], capture_output=True, timeout=20,
            shell=False, text=True)
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "").strip()[:400]
            errors.append({"file": rel, "kind": "js-syntax", "message": msg or "اجرا ناموفق"})
    except (OSError, subprocess.SubprocessError) as exc:
        msg = getattr(exc, "message", None) or str(exc)
        errors.append({"file": rel, "kind": "js-check-failed", "message": msg[:300]})


def _check_json(path: Path, rel: str, errors: list) -> None:
    if path.stat().st_size == 0:
        return
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        errors.append({"file": rel, "kind": "json-syntax", "message": str(exc)})


def _check_html(path: Path, rel: str, errors: list) -> None:
    low = path.read_bytes()
    if not low.strip():
        return
    text = low.decode("utf-8", errors="replace").lower()
    if "<html" not in text or "</html>" not in text:
        errors.append({"file": rel, "kind": "html-structure",
                       "message": "تگ <html> یا </html> یافت نشد"})
    if path.name == "index.html" and "<!doctype" not in text[:400]:
        errors.append({"file": rel, "kind": "html-doctype",
                       "message": "doctype اعلام نشده است — مرورگرها ممکن است حالت ناسازگار بدهند"})


if __name__ == "__main__":  # pragma: nocover — manual debug
    d = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    print(json.dumps(check(d), ensure_ascii=False, indent=2))
