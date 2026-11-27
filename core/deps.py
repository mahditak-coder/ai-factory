"""P6-4 — real dependency & runtime survey (read-only, offline-safe).

For a project this reports:
  * detected kind (static-web / python / node) — same facts the runner uses
  * python: declared requirements (requirements.txt) vs ACTUAL importable
    state: every non-stdlib import in the project's .py files is probed
    with a fresh interpreter (PROJECT PYTHONPATH, stripped env) and
    missing ones are listed.
  * node: package.json presence + declared deps + whether npm exists on
    this Termux installation.
  * dangerous-command refusal: NOTHING here ever executes project code
    (imports are resolved with `import x` inside a subprocess of the
    PROJECT'S interpreter without running its server), no shell, no
    network. Offline: reports only — never auto-installs.

Returns a plain dict, safe for the panel and for the auto-fix report.
"""

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from shutil import which

import core.paths as paths

_PY = sys.executable or "python3"

# stdlib probe set (3.x) — quick membership test via sys.stdlib_module_names


def _py_files(d: Path) -> list[Path]:
    return [f for f in sorted(d.rglob("*.py"))
            if "__pycache__" not in f.parts and ".factory" not in f.parts
            and not f.name.endswith(".pyc")]


def _declared_requirements(d: Path) -> list[str]:
    req = d / "requirements.txt"
    if not req.is_file():
        return []
    out = []
    for ln in req.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        # AF-20 fix (proven by the real pipeline): "//" is a comment here —
        # same filter install_deps and release.py already use; the scaffolded
        # fullstack template's "// deliberately empty" line must NOT be
        # parsed as a requirement.
        if ln and not ln.startswith(("#", "-", "//")):
            # AF-19 — local artifact lines (wheel/sdist path) normalize to
            # their distribution name so probe mapping stays consistent
            out.append(split_req(ln)[0])
    return [x for x in out if x]


def split_req(line: str) -> tuple[str, str]:
    """AF-19 — (name, version_spec) from a raw requirement line.
    Local artifact lines (wheel/sdist path) yield the distribution name
    so probe candidate mapping stays the same as index requirements."""
    text = line.split(";")[0].strip()
    low = text.lower()
    if low.endswith((".whl", ".tar.gz", ".zip")):
        return Path(text.replace("\\", "/")).name.split("-")[0], ""
    spec = ""
    for marker in ("==", ">=", "<=", "~=", "!=", ">", "<"):
        if marker in text:
            name, _, spec = text.partition(marker)
            spec = marker + spec
            break
    else:
        name = text
    return name.strip(), spec.strip()


def _version_of(dist_name: str) -> str | None:
    """Installed version from real metadata (in-process, offline-safe)."""
    try:
        import importlib.metadata as md
        return md.version(dist_name)
    except Exception:  # noqa: BLE001 — PackageNotFoundError / invalid name
        return None


def _version_ok(installed: str | None, spec: str) -> bool | None:
    """Honest version-spec check (==, ~=, >=, <=, >, <); None = unknown."""
    if not installed or not spec:
        return None
    op = spec[:2] if spec[:2] in ("==", ">=", "<=", "~=", "!=") \
        else (spec[:1] if spec[:1] in (">", "<") else "")
    ver = spec[len(op):].strip() if op else ""
    if not op or not ver:
        return None

    def key(v: str):
        try:
            return tuple(int(p) for p in v.split("."))
        except ValueError:
            return None
    a, b = key(installed), key(ver)
    if a is None or b is None:
        return None
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if op == ">=":
        return a >= b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    if op == "<":
        return a < b
    if op == "~=":            # compatible release: same prefix, >= floor
        return a[:len(b)] == b and a >= b
    return None


def requirements_status(d: Path) -> dict[str, dict]:
    """AF-19 — per-requirement three-state evidence (additive to survey).
    declared = present in requirements.txt; installed = importable probe;
    usable = installed AND the pinned version (if any) really matches.
    An entry is never reported installed unless the probe truly passed."""
    out: dict[str, dict] = {}
    req = d / "requirements.txt"
    if not req.is_file():
        return out
    for ln in req.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith(("#", "-", "//")):
            continue
        name, spec = split_req(ln)
        if not name:
            continue
        base = name.replace("-", "_").lower()
        candidates = {base, name.lower(), base.split("_")[0]}
        probe = any(_module_importable(c, d) for c in candidates)
        entry = {"declared": True, "installed": probe,
                 "state": "installed" if probe else "missing"}
        if probe and spec:
            ver = _version_of(name) or _version_of(base)
            ok = _version_ok(ver, spec)
            entry["version"] = ver
            entry["spec"] = spec
            entry["version_ok"] = ok
            if ok is False:
                entry["state"] = "incompatible"
            elif ok is None:
                entry["state"] = "installed"      # probe ok; spec unknown
        entry["usable"] = entry["state"] == "installed"
        out[ln] = entry
    return out


def _imports_required(d: Path) -> set[str]:
    """Top-level module names imported by project .py files (ast, safe)."""
    mods = set()
    for f in _py_files(d):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue                      # checker already reports this
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    mods.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 \
                    and node.module and node.col_offset == 0:
                mods.add(node.module.split(".")[0])
    return mods


def _module_importable(name: str, cwd: Path, extra_path: str | None = None) -> bool:
    """Fresh interpreter probe: `import name` (never touches the network,
    never runs project code paths other than module import resolution).
    A module import may EXECUTE its top-level code — but this is the same
    interpreter the project itself would use; isolated, stripped env.
    AF-19: `extra_path` appends one PYTHONPATH entry (isolated --target
    installs are probed in the env where the project would actually run)."""
    code = ("import importlib.util, sys\n"
            "sys.exit(0 if importlib.util.find_spec(%r) is not None else 1)"
            % name)
    pyenv = str(cwd) if not extra_path else str(cwd) + os.pathsep + str(extra_path)
    try:
        return subprocess.run(
            [_PY, "-c", code], capture_output=True, timeout=20,
            cwd=str(cwd), shell=False,
            env={"PYTHONPATH": pyenv, "PATH": "/data/data/com.termux"
                 "/files/usr/bin", "HOME": str(cwd),
                 "TMPDIR": str(cwd)}).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def importable(name: str, cwd: Path, extra_path: str | None = None) -> bool:
    """AF-19 — public single-source-of-truth import probe, reused by
    runner.install_deps post-install verification."""
    return _module_importable(name, cwd, extra_path)


def _stdlib_safe(name: str) -> bool:
    if not name or name.startswith("_"):
        return name == "_thread"
    try:
        return name in sys.stdlib_module_names
    except AttributeError:               # very old python
        return False


def _is_local_module(name: str, d: Path) -> bool:
    """Project-local module/package (server.py, app/, aiclient.py …)."""
    for cand in (d / f"{name}.py", d / name / "__init__.py",
                 d / name / (name + ".py")):
        if cand.is_file():
            return True
    # namespace package: a plain subdir of the project containing .py files
    ndir = d / name
    if ndir.is_dir() and any(ndir.glob("*.py")):
        return True
    # src/ or app/ layouts: any direct subdir with that name
    for sub in (d / "app", d / "src", d / "lib"):
        if (sub / f"{name}.py").is_file() or (sub / name / "__init__.py").is_file() \
                or (sub / name).is_dir():
            return True
    return False


def survey(project: str) -> dict:
    """Everything above, honest and complete, in one dict."""
    d = Path(paths.project_dir(project)).resolve()
    req_declared = _declared_requirements(d)
    imports = _imports_required(d)
    third_party = sorted(m for m in imports
                         if not _stdlib_safe(m)
                         and not _is_local_module(m, d))
    missing = [m for m in third_party if not _module_importable(m, d)]
    # declared-but-unmapped names are checked directly against site pkgs
    extra_missing = []
    for decl in req_declared:
        base = decl.split("[")[0].replace("-", "_").lower()
        candidates = {base, decl.lower(), base.split("_")[0]}
        if not any(_module_importable(c, d) for c in candidates):
            extra_missing.append(decl)

    pkg_json = d / "package.json"
    node_deps: list[str] = []
    package_json_ok = None
    if pkg_json.is_file():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            package_json_ok = True
            node_deps = sorted((data.get("dependencies") or {})
                               | (data.get("devDependencies") or {}))
        except ValueError:
            package_json_ok = False
    npm = which("npm") or which("yarn")
    node_modules = (d / "node_modules").is_dir()
    if package_json_ok and not node_modules:
        note_node = ("package.json دارد اما node_modules نصب نشده — "
                     f"{len(node_deps)} وابستگی اعلام‌شده"
                     + ("" if npm else "؛ npm هم موجود نیست"))
    elif package_json_ok:
        note_node = "node_modules موجود است"
    else:
        note_node = ""

    notes = []
    if missing:
        notes.append("importهای نصب‌نشده: " + ", ".join(missing))
    if extra_missing:
        notes.append("packages اعلام‌شده در requirements که نصب نیست: "
                     + ", ".join(extra_missing))
    if note_node:
        notes.append(note_node)
    return {
        "ok": True,
        "python_imports_third_party": third_party,
        "requirements_declared": req_declared,
        "missing_imports": missing,
        "missing_requirements": extra_missing,
        # AF-19 — per-requirement three-state evidence (declared/installed/usable)
        "requirements_status": requirements_status(d),
        "package_json": package_json_ok,
        "node_deps": node_deps,
        "node_modules_installed": node_modules,
        "npm_available": bool(npm),
        "notes": notes,
        "get_runnable": True if (d / "server.py").is_file() else None,
    }
