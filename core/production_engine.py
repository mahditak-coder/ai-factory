"""AF NEXT Phase 06 — verifiable production engine (composition only).

It composes EXISTING, REAL, DETECTED capabilities. It does not invent output,
does not execute specialists, does not bypass ToolExecutor/SecurityPolicy and
never calls a paid service without an explicit, consumed approval.

Every produced asset is recorded with provenance (tool/model/version), SHA-256,
honest cost basis and explicit limitations. SVG placeholders and synthesised
tones are always NON-PRODUCTION; screenshots are evidence-only.
"""
from __future__ import annotations

import core.config as config
from core import (engine_adapter, engine_discovery, image_assets,
                  production_assets, provider_policy, tool_registry,
                  visual_review)

ENGINE_MODES = ("android", "web", "backend", "ai-agent", "game-2d")


def capabilities() -> dict:
    """Read-only, honest capability snapshot of the real environment."""
    base = engine_discovery.report()
    media = {
        "ffprobe": production_assets.audio_capability(),
        "ffmpeg": {"status": engine_discovery.report()["tools"].get(
            "ffmpeg", {}).get("status", engine_discovery.NOT_VERIFIED)},
        "pillow": {"status": "AVAILABLE" if _pillow_ok() else
                   engine_discovery.BLOCKED,
                   "limits": ["non-generative processing only"]},
        "chromium_screenshot": visual_review.capability(),
    }
    return {"schema": "af-production-capability-1.0",
            "arch": base.get("arch", ""),
            "tool_status_model": base.get("status_model", []),
            "tools": base.get("tools", {}),
            "media": media, "memory_mb": base.get("memory_mb", {}),
            "notes": ["AVAILABLE means the tool was really detected and "
                      "version-probed; nothing is installed by this module",
                      "BLOCKED / NOT VERIFIED capabilities are never used"]}


def _pillow_ok() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def _record(project: str, rel: str, tool: str, model: str, version: str,
            cost: float, limits: list, ready: bool, evidence: dict,
            declared: dict | None = None) -> dict:
    return production_assets.record(
        project, rel,
        {"tool": tool, "model": model, "version": version,
         "credential_source": "none",
         "details": {"phase": "06", "engine": "production-engine"}},
        cost_usd=cost, limitations=limits, production_ready=ready,
        evidence=evidence, declared=declared)


def produce_image(project: str, prompt: str, provider: str, model: str,
                  *, operation: str = "generate", quality: str = "standard",
                  approval_id: str = "", confirmed_cost_usd=None,
                  rel_path: str = "assets/generated/image.png",
                  production_ready: bool = False, **kwargs) -> dict:
    """Explicit provider/model only; single call; validated and recorded."""
    decision = provider_policy.decide(
        project, provider, model, operation, quality, approval_id,
        confirmed_cost_usd, prompt=prompt,
        size=str(kwargs.get("size") or ""),
        references=list(kwargs.get("references") or []),
        region=str(kwargs.get("region") or ""))
    if not decision.get("ok"):
        return {"ok": False, "status": decision["status"],
                "reason": decision["reason"],
                "limits": decision.get("limits", [])}
    out = provider_policy.dispatch(decision, prompt, **kwargs)
    if not out.get("ok"):
        return {"ok": False, "status": out.get("status", "FAIL"),
                "reason": out.get("error") or out.get("reason", ""),
                "policy": out.get("policy", {})}
    rel = str(out.get("path") or rel_path)
    try:
        rec = _record(project, rel, out.get("provider") or provider,
                      out.get("model") or model,
                      str((out.get("evidence") or {}).get("version") or ""),
                      decision["estimated_cost_usd"], decision["limits"],
                      production_ready,
                      {"prompt_sha256": out.get("prompt_sha256", ""),
                       "response": out.get("evidence", {})},
                      {"production_ready_requested": production_ready,
                       "tool": out.get("provider") or provider})
    except (ValueError, OSError, production_assets.AssetStoreError) as exc:
        return {"ok": False, "status": "FAIL",
                "reason": "asset-recording-failed:%s" % exc,
                "policy": out.get("policy", {})}
    return {"ok": True, "status": "PASS", "asset": rec,
            "policy": out.get("policy", {}),
            "classification": rec["classification"],
            "production_ready": rec["production_ready"]}


def produce_audio(project: str, rel_path: str = "assets/audio/ambient.wav",
                  freq_hz: float = 440, duration_ms: int = 1200,
                  volume: float = 0.15) -> dict:
    """REAL PCM synthesis via the existing adapter; honestly NON-PRODUCTION."""
    cap = production_assets.audio_capability()
    if cap["status"] != "AVAILABLE" and not rel_path.lower().endswith(".wav"):
        return {"ok": False, "status": "BLOCKED",
                "reason": "no-real-audio-validator", "capability": cap}
    spec = tool_registry.registry().get("media.generate_wav")
    if spec is None:
        return {"ok": False, "status": "BLOCKED",
                "reason": "media-adapter-not-registered"}
    try:
        out = spec.adapter(project, {"path": rel_path, "freq_hz": freq_hz,
                                     "duration_ms": duration_ms,
                                     "volume": volume})
    except (OSError, ValueError) as exc:
        return {"ok": False, "status": "FAIL",
                "reason": "audio-generation-failed:%s" % exc}
    rel = str(out.get("path") or rel_path)
    try:
        rec = _record(project, rel, "stdlib-wave", "PCM sine", "1.0", 0.0,
                      ["local compute only; no paid API",
                       "synthesised PCM tone; musical quality NOT VERIFIED"],
                      False, out,
                      {"tool": "wave", "synthesised": True,
                       "quality_note": out.get("quality_note", "")})
    except (ValueError, OSError, production_assets.AssetStoreError) as exc:
        return {"ok": False, "status": "FAIL",
                "reason": "asset-recording-failed:%s" % exc}
    return {"ok": True, "status": "PASS", "asset": rec,
            "production_ready": False,
            "classification": "non-production-synthesised-tone",
            "validation": production_assets.validate_audio(
                __import__("core.paths", fromlist=["project_path"])
                .project_path(project, rel))}


def capture_evidence(project: str, url: str,
                     rel_out: str = "evidence/screenshot.png") -> dict:
    shot = visual_review.capture(project, url, rel_out)
    if not shot.get("ok"):
        return shot
    try:
        rec = _record(project, shot["path"], "chromium-headless", "Chromium",
                      "local", 0.0,
                      ["screenshot is visual evidence, not artwork",
                       "no network target; sanitised process environment"],
                      False, shot, {"evidence_only": True})
    except (ValueError, OSError, production_assets.AssetStoreError) as exc:
        return {"ok": False, "status": "FAIL",
                "reason": "screenshot-recording-failed:%s" % exc}
    return {"ok": True, "status": "PASS", "asset": rec,
            "production_ready": False, "classification": "evidence-only"}


def build(project: str, engine_type: str, opts: dict | None = None) -> dict:
    """Real build only through a DETECTED, SUPPORTED engine adapter."""
    if engine_type not in ENGINE_MODES:
        return {"ok": False, "status": "BLOCKED",
                "reason": "unknown-engine-mode:" + str(engine_type)[:60]}
    try:
        adapter = engine_adapter.adapter_for(engine_type)
    except (ValueError, KeyError, OSError) as exc:
        return {"ok": False, "status": "BLOCKED",
                "reason": "engine-adapter-unavailable:%s" % type(exc).__name__}
    try:
        support = adapter.supported(project)
    except (OSError, ValueError) as exc:
        return {"ok": False, "status": "BLOCKED",
                "reason": "engine-support-check-failed:%s" % type(exc).__name__}
    if not support.get("ok", support.get("supported")):
        return {"ok": False, "status": "BLOCKED",
                "reason": "engine-not-supported-in-environment",
                "support": support}
    result = adapter.build(project, opts or {})
    if not result.ok:
        return {"ok": False, "status": result.status,
                "reason": result.error, "evidence": result.evidence}
    hashes = {}
    for name, value in (result.artifacts or {}).items():
        try:
            from core import paths
            p = paths.project_path(project, str(value))
            if p.is_file():
                hashes[name] = {"path": str(value),
                                "sha256": production_assets.sha256_file(p),
                                "bytes": p.stat().st_size}
        except (ValueError, PermissionError, OSError):
            continue
    return {"ok": True, "status": result.status,
            "engine": engine_type, "artifacts": hashes,
            "evidence": result.evidence,
            "limits": ["build evidence is adapter-reported; external "
                       "reproducibility NOT VERIFIED"]}


def migrate(project: str, actor: str = "") -> dict:
    return production_assets.migrate(project, actor)


def rollback(project: str, migration_id: str = "") -> dict:
    return production_assets.rollback(project, migration_id)
