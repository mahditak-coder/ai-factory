"""AF NEXT Phase 4 — REAL environment capability discovery (read-only).

Status: AVAILABLE / INSTALLABLE / BLOCKED / NOT VERIFIED.
Nothing here installs anything or assumes a missing tool exists.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from functools import lru_cache

AVAILABLE, INSTALLABLE, BLOCKED, NOT_VERIFIED = (
    "AVAILABLE", "INSTALLABLE", "BLOCKED", "NOT VERIFIED")

_VERSION_ARGS = {
    "python3": ("--version",), "python": ("--version",),
    "node": ("--version",), "npm": ("--version",),
    "java": ("-version",), "javac": ("--version",),
    "aapt2": ("version",), "d8": ("--version",),
    "apksigner": ("version",), "keytool": ("-help",),
    "gradle": ("--version",), "adb": ("version",),
    "chromium-browser": ("--version",), "chromedriver": ("--version",),
    "ffmpeg": ("-version",),
}


@lru_cache(maxsize=1)
def _version(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        return ""
    try:
        p = subprocess.run([path, *_VERSION_ARGS.get(tool, ("--version",))],
                           capture_output=True, text=True, timeout=8,
                           shell=False, env=boundary_env())
        return ((p.stdout or "") + "\n" + (p.stderr or "")).strip().splitlines()[0][:180]
    except Exception:  # noqa: BLE001
        return ""


def boundary_env() -> dict:
    from core import boundary
    return boundary.clean_env()


@lru_cache(maxsize=1)
def report() -> dict:
    tools = {}
    for name in ("python3", "node", "npm", "java", "javac", "aapt2", "d8",
                 "apksigner", "keytool", "gradle", "adb",
                 "chromium-browser", "chromedriver", "ffmpeg"):
        path = shutil.which(name)
        tools[name] = {"status": AVAILABLE if path else NOT_VERIFIED,
                       "path": path or "", "version": _version(name)}
    # Technologies with a hard, documented environmental blocker.
    for name, why in (
        ("godot", "not installed; Termux availability NOT VERIFIED, no install performed"),
        ("unity", "desktop Editor + licence required; no headless path here"),
        ("electron", "native desktop packaging unavailable on aarch64 Termux"),
        ("tauri", "rust toolchain + desktop SDK required; not installed"),
        ("flutter", "SDK not installed; mobile/desktop packaging unverified"),
        ("swift", "Swift/xcode toolchain requires macOS"),
        ("xcodebuild", "iOS signing/build requires macOS + Xcode"),
    ):
        tools[name] = {"status": BLOCKED, "path": "", "version": "",
                       "reason": why}
    tools["bundletool"] = {
        "status": AVAILABLE if os.path.isfile(os.path.expanduser(
            "~/android-sdk-termux/bundletool.jar")) else NOT_VERIFIED,
        "path": os.path.expanduser("~/android-sdk-termux/bundletool.jar"),
        "version": "jar (local)"}
    tools["android.jar"] = {
        "status": AVAILABLE if os.path.isfile(os.path.expanduser(
            "~/android-sdk-termux/platforms/android-35/android.jar")) else NOT_VERIFIED,
        "path": os.path.expanduser(
            "~/android-sdk-termux/platforms/android-35/android.jar"),
        "version": "API 35"}
    mem = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith(("MemTotal:", "MemAvailable:")):
                    parts = line.split()
                    mem[parts[0].rstrip(":")] = int(parts[1])
    except OSError:
        pass
    return {"arch": platform.machine(), "tools": tools,
            "memory_mb": {"total": round(mem.get("MemTotal", 0) / 1024),
                          "available": round(mem.get("MemAvailable", 0) / 1024)},
            "status_model": (AVAILABLE, INSTALLABLE, BLOCKED, NOT_VERIFIED)}
