"""AF NEXT Phase 06 — ADR-013 local, headless, read-only visual review.

A screenshot is EVIDENCE, never a production artwork. The browser is used only
when it was really detected; its environment is sanitised (no credentials),
network targets are refused, and the output path is containment-checked and
validated as a real PNG.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import core.config as config
from core import boundary, paths

BLOCKED = "BLOCKED"
AVAILABLE = "AVAILABLE"
NOT_VERIFIED = "NOT VERIFIED"


def capability() -> dict:
    exe = shutil.which("chromium-browser") or shutil.which("chromium")
    if not exe:
        return {"status": NOT_VERIFIED, "tool": "chromium",
                "limits": ["no detected local browser; no screenshot taken"]}
    return {"status": AVAILABLE, "tool": "chromium", "path": exe,
            "limits": ["headless local rendering only; no network target",
                       "static screenshot; no interactive or animated review",
                       "no GPU acceleration in this environment",
                       "screenshot is evidence, NOT production artwork"]}


def _local_target(project: str, url: str) -> str:
    url = str(url or "").strip()
    if url in ("about:blank", ""):
        return "about:blank"
    prefix = "file://"
    if not url.startswith(prefix):
        raise PermissionError("network-targets-not-allowed:" + url[:80])
    rel = url[len(prefix):]
    base = paths.project_dir(project)
    candidate = (base / rel).resolve()
    if candidate != base and base not in candidate.parents:
        raise PermissionError("screenshot-target-escapes-project")
    return candidate.as_uri()


def capture(project: str, url: str,
            rel_out: str = "evidence/screenshot.png",
            width: int = 1280, height: int = 720) -> dict:
    cap = capability()
    if cap["status"] != AVAILABLE:
        return {"ok": False, "status": BLOCKED,
                "reason": "no-detected-local-browser", "capability": cap}
    try:
        target = _local_target(project, url)
        dst = paths.project_path(project, rel_out)
    except (PermissionError, ValueError, FileNotFoundError) as exc:
        return {"ok": False, "status": BLOCKED,
                "reason": "path-or-target-rejected:%s" % type(exc).__name__}
    if not dst.suffix.lower() == ".png":
        return {"ok": False, "status": BLOCKED,
                "reason": "screenshot-output-must-be-png"}
    dst.parent.mkdir(parents=True, exist_ok=True)
    w = max(320, min(3840, int(width)))
    h = max(240, min(2160, int(height)))
    cmd = [cap["path"], "--headless", "--disable-gpu", "--no-first-run",
           "--disable-extensions", "--hide-scrollbars",
           "--window-size=%d,%d" % (w, h),
           "--screenshot=" + str(dst), target]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45,
                              shell=False, env=boundary.clean_env())
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "status": BLOCKED,
                "reason": "browser-execution-failed:%s" % type(exc).__name__}
    if proc.returncode != 0 or not dst.is_file():
        return {"ok": False, "status": BLOCKED,
                "reason": "browser-did-not-produce-screenshot",
                "stderr": (proc.stderr or "")[:400]}
    from core import image_assets
    try:
        info = image_assets.validate_png(dst.read_bytes())
    except (ValueError, OSError) as exc:
        return {"ok": False, "status": "FAIL",
                "reason": "screenshot-not-a-valid-png:%s" % exc}
    return {"ok": True, "status": AVAILABLE,
            "path": dst.relative_to(paths.project_dir(project)).as_posix(),
            "validation": info, "target": target,
            "capability": cap,
            "classification": "evidence-only", "production_ready": False}
