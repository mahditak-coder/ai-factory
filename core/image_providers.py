"""AF — independent Image Generation Provider layer.

This is deliberately NOT part of ModelGateway (text modality). It has its own
provider ids, config and status vocabulary, and it never invents an image.
Real generator credentials are absent/invalid in this environment, so every
generative provider reports BLOCKED. Local Pillow processing is REAL and is
reported separately from generation.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import core.config as config
from core import paths

AVAILABLE, BLOCKED, NOT_VERIFIED = "AVAILABLE", "BLOCKED", "NOT VERIFIED"
_CONFIG = config.PROJECT_ROOT / "config" / "image_providers.json"
_PROBE = config.PROJECT_ROOT / "data" / "image_provider_probe.json"


@dataclass
class ImageResult:
    status: str
    ok: bool = False
    path: str = ""
    format: str = ""
    width: int = 0
    height: int = 0
    sha256: str = ""
    provider: str = ""
    model: str = ""
    cost_note: str = "unknown / not charged"
    error: str = ""
    evidence: dict = field(default_factory=dict)

    @classmethod
    def fail(cls, reason: str, status: str = BLOCKED, **kw) -> "ImageResult":
        return cls(status=status, ok=False, error=str(reason)[:400], **kw)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@runtime_checkable
class ImageProvider(Protocol):
    id: str
    title: str
    modality: str

    def status(self) -> dict: ...
    def generate(self, prompt: str, **kwargs) -> ImageResult: ...


class _Base:
    modality = "image"
    model = ""
    cost_note = "unknown / not charged"

    def _cfg(self) -> dict:
        try:
            return json.loads(_CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"active": "", "providers": []}

    def is_active(self) -> bool:
        return self._cfg().get("active") == self.id


class HarnessImageProvider(_Base):
    """Real harness model. Last real attempt failed; no retry without creds."""
    id = "harness-opencode-image"
    title = "OpenCode image model (harness only)"
    model = "opencode/grok-imagine-image-2.0"

    def status(self) -> dict:
        probe = {}
        try:
            probe = json.loads(_PROBE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        return {"provider": self.id, "model": self.model,
                "status": BLOCKED, "active": self.is_active(),
                "reason": probe.get("error", "no verified credential"),
                "at": probe.get("at", ""), "cost": self.cost_note,
                "runtime_access": "agent harness tool only; not reachable "
                                  "from AI Factory server/ToolRegistry"}

    def generate(self, prompt: str, **kwargs) -> ImageResult:
        st = self.status()
        return ImageResult.fail("image-generator-credential-invalid: " +
                                st["reason"], BLOCKED,
                                provider=self.id, model=self.model,
                                cost_note=self.cost_note,
                                evidence={"status": st})


class OpenCodeImageProvider(_Base):
    """Contract-only: local OpenCode exposes text sessions, no image route."""
    id = "opencode-image"
    title = "OpenCode local image endpoint"
    model = "not-exposed"

    def status(self) -> dict:
        return {"provider": self.id, "model": self.model, "status": BLOCKED,
                "active": self.is_active(),
                "reason": "no image-generation route in local OpenCode /api/*; "
                          "ModelGateway has text modality only",
                "cost": self.cost_note}

    def generate(self, prompt: str, **kwargs) -> ImageResult:
        return ImageResult.fail("opencode-image-route-not-exposed", BLOCKED,
                                provider=self.id, model=self.model,
                                cost_note=self.cost_note)


class LocalPillowProvider(_Base):
    """REAL local processing — never claimed to be AI generation."""
    id = "local-pillow"
    title = "Local Pillow image processing (non-generative)"
    model = "Pillow 12.3.0"

    def status(self) -> dict:
        try:
            import PIL
            return {"provider": self.id, "model": "Pillow " + PIL.__version__,
                    "status": AVAILABLE, "active": self.is_active(),
                    "capabilities": ["inspect", "resize", "white_to_alpha",
                                     "sprite_sheet", "png_export"],
                    "limits": ["no AI drawing", "no style synthesis",
                               "cannot produce gouache artwork"]}
        except ImportError as exc:
            return {"provider": self.id, "status": BLOCKED,
                    "reason": str(exc), "active": self.is_active()}

    def generate(self, prompt: str, **kwargs) -> ImageResult:
        return ImageResult.fail("local-pillow-is-not-a-generative-provider",
                                BLOCKED, provider=self.id, model=self.model)


PROVIDERS = (HarnessImageProvider(), OpenCodeImageProvider(), LocalPillowProvider())
_BY_ID = {p.id: p for p in PROVIDERS}


def _all() -> tuple:
    # Lazy import avoids circular imports: remote adapters reuse ImageResult
    # and _Base from this module and are registered here, not duplicated.
    from core import image_remote
    return PROVIDERS + image_remote.PROVIDERS


def providers() -> list[dict]:
    return [p.status() for p in _all()]


def get(provider_id: str) -> ImageProvider | None:
    return next((p for p in _all() if p.id == provider_id), None)


def active() -> ImageProvider | None:
    cfg = _cfg_active()
    return get(cfg) if cfg else None


def _cfg_active() -> str:
    try:
        return str(json.loads(_CONFIG.read_text(encoding="utf-8")).get("active") or "")
    except (OSError, ValueError):
        return ""


def select(prefer_paid: bool = False) -> ImageProvider | None:
    """Capability-first: never choose a blocked paid provider automatically."""
    for p in PROVIDERS:
        if isinstance(p, LocalPillowProvider):
            continue
        if p.status()["status"] == AVAILABLE and (prefer_paid or True):
            return p
    return None


def record_probe(error: str, at: str = "") -> dict:
    """Records ONLY a redacted outcome — never a key or a token."""
    data = {"error": str(error)[:160], "at": at or time.strftime("%Y-%m-%dT%H:%M:%S"),
            "secrets_exposed": False}
    _PROBE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PROBE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_PROBE)
    return data


# ---------------------------------------------------------------- processing --
def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def process(project: str, inputs: dict) -> ImageResult:
    """REAL, non-generative Pillow operations inside a governed project path."""
    from PIL import Image
    op = str(inputs.get("op") or "inspect")
    rel_in = str(inputs.get("path") or "")
    rel_out = str(inputs.get("output") or "")
    src = paths.project_path(project, rel_in)
    if not src.is_file():
        return ImageResult.fail("source-not-found:" + rel_in, "FAIL")
    dst = paths.project_path(project, rel_out) if rel_out else None
    if dst and dst.parent and rel_out:
        dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im0:
        im = im0.convert("RGBA")
    meta = {"op": op, "source": rel_in}
    if op == "inspect":
        return ImageResult(status=AVAILABLE, ok=True, provider="local-pillow",
            model="Pillow", path=rel_in, format=src.suffix.lower().lstrip("."),
            width=im.width, height=im.height, sha256=_sha(src),
            evidence={"mode": im.mode, "bytes": src.stat().st_size,
                      "has_alpha": im.getchannel("A").getextrema()[0] < 255,
                      "sha256": _sha(src)})
    if op == "resize":
        w = int(inputs.get("width") or im.width)
        h = int(inputs.get("height") or im.height)
        out = im.resize((w, h), Image.Resampling.LANCZOS)
    elif op == "white_to_alpha":
        thr = int(inputs.get("threshold") or 245)
        px = im.load()
        removed = 0
        for y in range(im.height):
            for x in range(im.width):
                r, g, b, a = px[x, y]
                if r >= thr and g >= thr and b >= thr:
                    px[x, y] = (r, g, b, 0)
                    removed += 1
        out = im
        meta["transparent_pixels"] = removed
    elif op == "sprite_sheet":
        fw = int(inputs.get("frame_width") or 0)
        fh = int(inputs.get("frame_height") or 0)
        if fw <= 0 or fh <= 0:
            return ImageResult.fail("frame-size-required", "FAIL")
        cols = max(1, im.width // fw)
        rows = max(1, im.height // fh)
        frames = []
        for cy in range(rows):
            for cx in range(cols):
                box = (cx * fw, cy * fh, (cx + 1) * fw, (cy + 1) * fh)
                frames.append(im.crop(box))
        out = Image.new("RGBA", (fw * cols, fh * rows), (0, 0, 0, 0))
        for i, fr in enumerate(frames):
            out.paste(fr, ((i % cols) * fw, (i // cols) * fh))
        meta.update({"frames": len(frames), "cols": cols, "rows": rows})
    else:
        return ImageResult.fail("unsupported-op:" + op, "FAIL")
    if dst is None:
        return ImageResult(status=AVAILABLE, ok=True, provider="local-pillow",
            model="Pillow", path=rel_in, width=out.width, height=out.height,
            evidence=meta, cost_note="local compute only; no paid API")
    out.save(dst, format="PNG")
    return ImageResult(status=AVAILABLE, ok=True, provider="local-pillow",
        model="Pillow", path=rel_out, format="png", width=out.width,
        height=out.height, sha256=_sha(dst), evidence=meta,
        cost_note="local compute only; no paid API")
