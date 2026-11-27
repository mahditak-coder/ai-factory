"""REAL generated-asset storage: project-scoped, versioned, never overwritten."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from core import paths

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def validate_png(data: bytes) -> dict:
    if not data or not data.startswith(PNG_MAGIC):
        raise ValueError("not-a-png")
    from PIL import Image
    import io
    with Image.open(io.BytesIO(data)) as im:
        im.verify()
    with Image.open(io.BytesIO(data)) as im:
        size = im.size
        mode = im.mode
    return {"width": size[0], "height": size[1], "mode": mode,
            "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def save(project: str, rel_base: str, data: bytes, meta: dict) -> dict:
    """Save a REAL PNG with a monotonic version; never replace an existing file."""
    info = validate_png(data)
    rel = str(rel_base or "assets/generated/image.png")
    if not rel.lower().endswith(".png"):
        rel += ".png"
    base = paths.project_path(project, rel)              # path restriction
    version = 1
    candidate = base
    while candidate.exists():
        version += 1
        candidate = base.with_name("%s-v%d.png" % (base.stem, version))
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(data)
    record = {"schema": "af-image-asset-1.0", "project": project,
              "path": candidate.relative_to(paths.project_dir(project)).as_posix(),
              "version": version, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "sha256": info["sha256"], "dimensions": [info["width"], info["height"]],
              "mode": info["mode"], "bytes": info["bytes"], **meta}
    sidecar = Path(str(candidate) + ".json")
    if not sidecar.exists():
        sidecar.write_text(json.dumps(record, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    return record
