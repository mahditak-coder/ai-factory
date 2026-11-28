"""AF NEXT Phase 06 — ADR-013 local, headless, read-only visual review.

A screenshot is EVIDENCE, never a production artwork. The browser is used only
when a REAL local browser was detected AND the OS can actually deny network
access for the whole process tree. Validating only the initial URL is NOT
enough, so an OS network namespace (`unshare -n`) is probed at runtime: if it
cannot be demonstrated, screenshot execution is BLOCKED and the limitation is
reported honestly. No tool is installed and no security control is weakened.

Environment is sanitised via `boundary.clean_env`, output paths are checked by
`paths.project_path`, and the result is validated as a real PNG.
"""
from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from core import boundary, paths

BLOCKED = "BLOCKED"
AVAILABLE = "AVAILABLE"
NOT_VERIFIED = "NOT VERIFIED"


def browser_capability() -> dict:
    exe = shutil.which("chromium-browser") or shutil.which("chromium")
    if not exe:
        return {"status": NOT_VERIFIED, "tool": "chromium",
                "limits": ["no detected local browser; no screenshot taken"]}
    return {"status": AVAILABLE, "tool": "chromium", "path": exe,
            "limits": ["headless local rendering only",
                       "static screenshot; no interactive review",
                       "no GPU acceleration in this environment",
                       "screenshot is evidence, NOT production artwork"]}


@lru_cache(maxsize=1)
def network_isolation_capability() -> dict:
    """Prove the WHOLE Chromium process can be denied network access."""
    exe = shutil.which("unshare")
    if not exe:
        return {"status": BLOCKED, "mechanism": "", "path": "",
                "reason": "os-network-isolation-tool-not-detected",
                "limits": ["no unshare/bubblewrap/firejail available; "
                           "URL-only validation is insufficient"]}
    try:
        proc = subprocess.run([exe, "-n", "true"], capture_output=True,
                              text=True, timeout=10, shell=False,
                              env=boundary.clean_env())
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": BLOCKED, "mechanism": "", "path": exe,
                "reason": "isolation-probe-failed:" + type(exc).__name__,
                "limits": ["network namespace could not be demonstrated"]}
    if proc.returncode != 0:
        detail = ((proc.stderr or proc.stdout or "").strip().splitlines()
                  or ["unknown"])[0][:160]
        return {"status": BLOCKED, "mechanism": "", "path": exe,
                "reason": "os-network-namespace-unavailable:" + detail,
                "limits": ["kernel/container denied unshare -n; "
                           "refusing to run a browser with network access"]}
    return {"status": AVAILABLE, "mechanism": "unshare -n", "path": exe,
            "limits": ["network namespace denied for the entire process tree; "
                       "file:// targets still work"]}


def capability() -> dict:
    """Combined browser + isolation status (read-only)."""
    browser = browser_capability()
    isolation = network_isolation_capability()
    return {"status": (AVAILABLE if browser["status"] == AVAILABLE and
                       isolation["status"] == AVAILABLE else BLOCKED),
            "tool": "chromium", "browser": browser, "isolation": isolation,
            "limits": browser.get("limits", []) + isolation.get("limits", [])}


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
    browser = browser_capability()
    iso = network_isolation_capability()
    if browser["status"] != AVAILABLE:
        return {"ok": False, "status": BLOCKED,
                "reason": "no-detected-local-browser", "capability": capability()}
    if iso["status"] != AVAILABLE:
        # Honest block: URL validation alone cannot stop subresources,
        # redirects, WebSockets, service workers or navigation.
        return {"ok": False, "status": BLOCKED,
                "reason": "network-isolation-unavailable:" +
                          str(iso.get("reason")),
                "capability": capability(),
                "note": "screenshot NOT taken; no network-exposed browser "
                        "was started"}
    try:
        target = _local_target(project, url)
        dst = paths.project_path(project, rel_out)
    except (PermissionError, ValueError, FileNotFoundError) as exc:
        return {"ok": False, "status": BLOCKED,
                "reason": "path-or-target-rejected:%s" % type(exc).__name__}
    if dst.suffix.lower() != ".png":
        return {"ok": False, "status": BLOCKED,
                "reason": "screenshot-output-must-be-png"}
    dst.parent.mkdir(parents=True, exist_ok=True)
    w = max(320, min(3840, int(width)))
    h = max(240, min(2160, int(height)))
    chromium = [browser["path"], "--headless", "--disable-gpu",
                "--no-first-run", "--disable-extensions", "--hide-scrollbars",
                "--disable-background-networking", "--disable-component-update",
                "--disable-sync", "--disable-default-apps",
                "--disable-domain-reliability", "--no-proxy-server",
                "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost",
                "--window-size=%d,%d" % (w, h), "--screenshot=" + str(dst),
                target]
    cmd = [iso["path"], "-n"] + chromium
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
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
            "capability": capability(), "isolation": iso,
            "classification": "evidence-only", "production_ready": False}
