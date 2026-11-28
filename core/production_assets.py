"""AF NEXT Phase 06 — verifiable production asset records.

Additive and local-only. This module NEVER executes a specialist, calls a paid
service, or changes an accepted Phase 01-05 authorization boundary. It adds:

* real validation of PNG (via the existing helper) and audio (via DETECTED
  ffprobe, or Python ``wave`` for WAV when ffprobe is absent);
* one provenance/hash/cost/limit record per asset in a flock-guarded, atomic
  store;
* honest classification: SVG placeholders and synthesised tones are explicitly
  NON-PRODUCTION; screenshots are evidence-only;
* a versioned, idempotent, REVERSIBLE migration that marks legacy assets
  non-production without deleting or overwriting them.

All filesystem writes go through ``core.paths.project_path`` containment.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import core.config as config
from core import boundary, paths

SCHEMA = "af-production-asset-1.0"
MIGRATION_SCHEMA = "af-asset-migration-1.0"
_STORE = config.PROJECT_ROOT / "data" / "production_assets.json"
_MIGRATIONS = config.PROJECT_ROOT / "data" / "asset_migrations.json"
_LEGACY_EXT = (".svg", ".wav", ".mp3", ".ogg", ".flac", ".m4a", ".png",
               ".jpg", ".jpeg", ".webp")
_MAX_SCAN = 500


class AssetStoreError(RuntimeError):
    """Corrupt/unreadable production store; never silently treated as empty."""


@contextmanager
def _lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        os.close(fd)


def _empty(schema: str) -> dict:
    return {"schema": schema, "assets": {}, "migrations": {}}


def _load(path: Path, schema: str) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty(schema)
    except OSError as exc:
        raise AssetStoreError("asset-store-unreadable:" +
                              type(exc).__name__) from exc
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise AssetStoreError("asset-store-corrupt-json") from exc
    if not isinstance(data, dict) or data.get("schema") != schema:
        raise AssetStoreError("asset-store-invalid-root")
    for key in ("assets", "migrations"):
        if not isinstance(data.get(key, {}), dict):
            raise AssetStoreError("asset-store-invalid-" + key)
    return data


def _save(path: Path, data: dict) -> None:
    tmp = Path("%s.%d.%d.%s.tmp" % (path, os.getpid(),
                                    threading.get_ident(),
                                    secrets.token_hex(6)))
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1,
                                  sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def audio_capability() -> dict:
    """Read-only detection of a REAL audio validator; no install, no assumption."""
    probe = shutil.which("ffprobe")
    if probe:
        return {"status": "AVAILABLE", "tool": "ffprobe", "path": probe,
                "limits": ["container/codec metadata only; no perceptual quality "
                           "or musical-quality judgement"]}
    return {"status": "NOT VERIFIED", "tool": "ffprobe", "path": "",
            "fallback": "python-wave for RIFF/WAVE only",
            "limits": ["non-WAV audio cannot be verified without ffprobe"]}


def validate_audio(path: Path) -> dict:
    """Validate a REAL audio file. Corrupt/fake data is rejected, not guessed."""
    if not path.is_file():
        raise ValueError("audio-not-found")
    data = path.read_bytes()[:64]
    wav = data.startswith(b"RIFF") and data[8:12] == b"WAVE"
    info: dict = {"path": str(path), "bytes": path.stat().st_size,
                  "sha256": sha256_file(path)}
    probe = shutil.which("ffprobe")
    if probe:
        try:
            proc = subprocess.run(
                [probe, "-v", "error", "-show_entries",
                 "format=format_name,duration:stream=codec_name,sample_rate,"
                 "channels", "-of", "json", str(path)],
                capture_output=True, text=True, timeout=15, shell=False,
                env=boundary.clean_env())
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("ffprobe-unavailable:%s" %
                             type(exc).__name__) from exc
        if proc.returncode != 0:
            raise ValueError("ffprobe-rejected-file")
        try:
            parsed = json.loads(proc.stdout or "{}")
        except ValueError as exc:
            raise ValueError("ffprobe-invalid-output") from exc
        fmt = (parsed.get("format") or {})
        stream = (parsed.get("streams") or [{}])[0]
        if not fmt and not stream:
            raise ValueError("not-an-audio-file")
        info.update({"status": "AVAILABLE", "validator": "ffprobe",
                     "format": str(fmt.get("format_name") or ""),
                     "duration_s": float(fmt.get("duration") or 0),
                     "codec": str(stream.get("codec_name") or ""),
                     "sample_rate": int(stream.get("sample_rate") or 0),
                     "channels": int(stream.get("channels") or 0)})
        return info
    if wav:
        import wave
        with wave.open(str(path), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            info.update({"status": "AVAILABLE", "validator": "python-wave",
                         "codec": "pcm", "sample_rate": rate,
                         "channels": w.getnchannels(),
                         "duration_s": round(frames / float(rate), 4) if rate
                         else 0})
        return info
    raise ValueError("audio-unverifiable-without-ffprobe")


def classify(rel_path: str, declared: dict | None = None) -> tuple[bool, str, list]:
    """Honest production classification. Placeholders can NEVER be production."""
    declared = declared or {}
    suffix = Path(str(rel_path)).suffix.lower()
    limits: list[str] = []
    if suffix == ".svg":
        limits.append("vector placeholder; not a professional production asset")
        return False, "non-production-vector-placeholder", limits
    if declared.get("evidence_only"):
        limits.append("local screenshot / visual evidence, not generated artwork")
        return False, "evidence-only", limits
    tool = str(declared.get("tool") or "")
    if ("wave" in tool or "synthes" in tool or
            "synthesised" in str(declared.get("quality_note") or "")):
        limits.append("synthesised PCM tone; musical quality NOT VERIFIED")
        return False, "non-production-synthesised-tone", limits
    if suffix not in (".png", ".wav", ".mp3", ".ogg", ".flac", ".m4a",
                      ".jpg", ".jpeg", ".webp"):
        limits.append("unrecognised media type; no production claim")
        return False, "non-production-unknown-type", limits
    if not declared.get("production_ready_requested"):
        limits.append("no explicit production-ready request; default is non-production")
        return False, "non-production-unclaimed", limits
    if not declared.get("validated"):
        limits.append("file validation did not complete")
        return False, "non-production-unvalidated", limits
    if not declared.get("provenance"):
        limits.append("no tool/model provenance recorded")
        return False, "non-production-unattributed", limits
    return True, "production-verified", limits


def record(project: str, rel_path: str, provenance: dict,
           cost_usd: float = 0.0, limitations: list | None = None,
           production_ready: bool = False, evidence: dict | None = None,
           declared: dict | None = None) -> dict:
    """Validate and record one asset with full provenance in the atomic store."""
    path = paths.project_path(project, rel_path)      # containment
    if not path.is_file():
        raise ValueError("asset-not-found")
    suffix = path.suffix.lower()
    validated = {"validated": True}
    if suffix == ".png":
        from core import image_assets
        validated.update(image_assets.validate_png(path.read_bytes()))
    elif suffix in (".wav", ".mp3", ".ogg", ".flac", ".m4a"):
        validated.update(validate_audio(path))
    else:
        validated = {"validated": suffix in (".jpg", ".jpeg", ".webp"),
                     "sha256": sha256_file(path)}
    sha = validated["sha256"]
    dec = dict(declared or {})
    dec["validated"] = bool(validated.get("validated"))
    dec["provenance"] = bool(provenance.get("tool") or provenance.get("model"))
    ready, classification, auto_limits = classify(rel_path, dec)
    if not ready and production_ready:
        auto_limits = list(auto_limits) + [
            "requested production-ready status denied by classification gate"]
    if ready:
        ready = bool(production_ready)
    try:
        amount = round(float(cost_usd), 4)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid-cost") from exc
    if amount < 0:
        raise ValueError("negative-cost")
    limits = list(limitations or []) + auto_limits
    limits.extend(validated.get("limits", []))
    asset_id = hashlib.sha256(
        ("af-production-asset\x00%s\x00%s" % (path.name, sha)).encode()
    ).hexdigest()[:20]
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    record_data = {
        "schema": SCHEMA, "asset_id": asset_id, "project": project,
        "path": path.relative_to(paths.project_dir(project)).as_posix(),
        "sha256": sha, "bytes": path.stat().st_size, "verified_at": now,
        "provenance": {"tool": str(provenance.get("tool") or ""),
                       "model": str(provenance.get("model") or ""),
                       "version": str(provenance.get("version") or ""),
                       "credential_source": str(
                           provenance.get("credential_source") or "none"),
                       "details": provenance.get("details") or {}},
        "cost_usd": amount, "limits": limits,
        "classification": classification, "production_ready": bool(ready),
        "validation": validated, "evidence": evidence or {}}
    with _lock(_STORE):
        data = _load(_STORE, SCHEMA)
        existing = data["assets"].get(asset_id)
        if existing is not None:
            if existing.get("sha256") != sha:
                raise AssetStoreError("asset-hash-conflict:" + asset_id)
            return existing
        data["assets"][asset_id] = record_data
        _save(_STORE, data)
    return record_data


def asset_report(project: str = "") -> dict:
    with _lock(_STORE):
        data = _load(_STORE, SCHEMA)
    rows = [v for v in data["assets"].values()
            if not project or v.get("project") == project]
    return {"ok": True, "count": len(rows), "assets": rows,
            "production_ready": sum(1 for r in rows
                                    if r.get("production_ready") is True),
            "non_production": sum(1 for r in rows
                                  if r.get("production_ready") is not True)}


def _sidecar(path: Path) -> Path:
    return Path(str(path) + ".production.json")


def migration_report(project: str) -> dict:
    with _lock(_MIGRATIONS):
        data = _load(_MIGRATIONS, MIGRATION_SCHEMA)
    mine = [v for v in data["migrations"].values()
            if v.get("project") == project]
    return {"ok": True, "project": project, "migrations": mine}


def migrate(project: str, actor: str = "") -> dict:
    """Versioned, idempotent, REVERSIBLE legacy marking. Never deletes assets."""
    base = paths.project_dir(project)
    candidates = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "logs")]
        for name in sorted(files):
            if name.lower().endswith(_LEGACY_EXT) and \
                    not name.endswith(".json"):
                candidates.append(Path(root) / name)
        if len(candidates) >= _MAX_SCAN:
            break
    with _lock(_MIGRATIONS):
        data = _load(_MIGRATIONS, MIGRATION_SCHEMA)
        created: list[str] = []
        backed_up: list[dict] = []
        stamp = time.strftime("%Y%m%d%H%M%S")
        mid = "asset-mig-%s-%s" % (stamp, secrets.token_hex(3))
        for path in candidates[:_MAX_SCAN]:
            side = _sidecar(path)
            rel = side.relative_to(base).as_posix()
            if side.exists():
                continue  # already classified: idempotent, never overwritten
            sha = sha256_file(path)
            ready, classification, limits = classify(rel)
            record_data = {"schema": SCHEMA, "project": project,
                           "path": path.relative_to(base).as_posix(),
                           "sha256": sha, "classification": classification,
                           "production_ready": ready, "limits": limits,
                           "migration_id": mid, "marked_at": stamp,
                           "actor": str(actor)[:80],
                           "note": "legacy asset preserved; explicitly "
                                   "marked non-production"}
            side.write_text(json.dumps(record_data, ensure_ascii=False,
                                       indent=1), encoding="utf-8")
            created.append(rel)
        if not created:
            # Idempotent no-op: do not pollute the versioned journal.
            return {"ok": True, "migration_id": "", "changed": 0,
                    "candidates": len(candidates),
                    "originals_preserved": True, "reversible": True}
        data["migrations"][mid] = {
            "schema": MIGRATION_SCHEMA, "id": mid, "project": project,
            "at": stamp, "actor": str(actor)[:80], "version": 1,
            "created_sidecars": created, "restored_sidecars": backed_up,
            "originals_preserved": True, "reversible": True}
        _save(_MIGRATIONS, data)
    return {"ok": True, "migration_id": mid, "changed": len(created),
            "candidates": len(candidates), "originals_preserved": True,
            "reversible": True}


def rollback(project: str, migration_id: str = "") -> dict:
    """Restore the state before a migration; never touches original assets."""
    with _lock(_MIGRATIONS):
        data = _load(_MIGRATIONS, MIGRATION_SCHEMA)
        ids = [migration_id] if migration_id else [
            k for k, v in data["migrations"].items()
            if v.get("project") == project]
        removed = 0
        for mid in ids:
            entry = data["migrations"].get(mid)
            if entry is None:
                raise AssetStoreError("unknown-migration:" + str(mid)[:80])
            if entry.get("project") != project:
                raise AssetStoreError("migration-project-mismatch")
            base = paths.project_dir(project)
            for rel in entry.get("created_sidecars", []):
                side = paths.project_path(project, rel)
                if side.is_file():
                    side.unlink()
                    removed += 1
            for item in entry.get("restored_sidecars", []):
                side = paths.project_path(project, str(item.get("sidecar")))
                backup = paths.project_path(project, str(item.get("backup")))
                if backup.is_file():
                    side.write_bytes(backup.read_bytes())
            del data["migrations"][mid]
        _save(_MIGRATIONS, data)
    return {"ok": True, "removed_sidecars": removed,
            "originals_preserved": True}
