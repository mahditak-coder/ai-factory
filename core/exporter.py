"""Factory exporter — real ZIP output of a project plus integrity checks.

Exclusions (enforced):
  * runtime / junk: .factory, __pycache__, node_modules, .git, *.pyc, *.log,
                    *.pid, *.out, server.out, opencode.json (dev-time config)
  * secrets:        *.token, *.pem, *.key, .env
The export path is always stored under exports/ and verified with
zipfile.testzip() before being reported as usable.
Backups (pre-fix / pre-delete) are the same content under backups/<project>/.
"""

import shutil
import time
import zipfile
from pathlib import Path

import core.config as config
from core import paths

log = __import__("logging").getLogger("ai-factory.exporter")


def _allowed(f: Path, project_dir: Path) -> bool:
    if config.EXPORT_SECRET_NAMES.intersection({f.name}) or \
       config.EXPORT_SECRET_NAMES.intersection(f.parts):
        return False
    if f.name in config.EXPORT_EXCLUDE_NAMES or \
       config.EXPORT_EXCLUDE_NAMES.intersection(f.parts):
        return False
    if f.suffix.lower() in config.EXPORT_SECRET_SUFFIXES:
        return False
    if f.suffix.lower() in config.EXPORT_EXCLUDE_SUFFIXES:
        return False
    return True


def export(project: str) -> dict:
    d = paths.project_dir(project)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = config.EXPORTS_DIR / f"{project}-{stamp}.zip"
    res = _zip_project(d, out)
    log.info("factory export: %s (%d files, %d bytes, bad=%s)",
             out.name, res["files"], res["size_bytes"], res["integrity_bad_file"])
    return res


def _testzip(path: Path) -> str | None:
    with zipfile.ZipFile(path) as zf:
        return zf.testzip()


def _zip_project(d: Path, out: Path) -> dict:
    files = [f for f in sorted(d.rglob("*")) if f.is_file() and _allowed(f, d)]
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, arcname=f.relative_to(d).as_posix())
    bad = _testzip(out)
    return {"path": str(out), "files": len(files),
            "size_bytes": out.stat().st_size, "integrity_bad_file": bad}


def backup(project: str) -> dict:
    d = paths.project_dir(project)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = config.BACKUPS_DIR / project / f"{project}-{stamp}.zip"
    res = _zip_project(d, out)
    log.info("factory backup: %s", out)
    return res


def list_exports() -> list[dict]:
    out = []
    for p in sorted(config.EXPORTS_DIR.glob("*.zip")):
        try:
            bad = _testzip(p)
        except (OSError, zipfile.BadZipFile) as exc:
            bad = f"unopenable: {exc}"
        out.append({"path": str(p), "name": p.name,
                    "size_bytes": p.stat().st_size,
                    "integrity": "ok" if bad is None else f"broken ({bad})"})
    return out


def to_downloads(path: str) -> dict:
    """Best-effort copy into the phone's Downloads directory.

    On Android/Termux this requires storage permission (termux-setup-storage);
    if unavailable we say so honestly and the file stays in exports/.
    """
    src = Path(path)
    for candidate in Path("/sdcard/Download"), Path("/storage/emulated/0/Download"):
        if candidate.is_dir():
            dst = candidate / src.name
            try:
                shutil.copyfile(src, dst)
                return {"applied": True, "path": str(dst)}
            except OSError as exc:
                return {"applied": False,
                        "note": f"کپی انجام نشد ({exc}) — فایل در exports/ باقی است"}
        continue
    return {"applied": False,
            "note": "پوشه Downloads در دسترس نیست (termux-setup-storage را اجرا کنید)"}
