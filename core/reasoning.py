"""AF-51 — real reasoning-level control built on OpenCode model variants.

OpenCode v2.2.6 exposes ``model.variants`` (e.g. low/medium/high/max with
``settings.reasoningEffort``). This module maps five user levels to a REAL
variant of the ACTUAL selected model. If the model cannot support a level it
says so honestly — no fake success. Auto picks from real capability + task
complexity. The default level persists in data/settings.json (0600, atomic).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import core.config as config

LEVELS = ("auto", "low", "medium", "high", "maximum")
_STORE = config.PROJECT_ROOT / "data" / "settings.json"
_LOCK = threading.RLock()

# Preferred variant ids for each user level, in order of acceptability.
_PREFER = {
    "low": ("low", "minimal", "none"),
    "medium": ("medium", "low", "minimal"),
    "high": ("high", "xhigh", "medium"),
    "maximum": ("max", "xhigh", "high"),
}


def _load() -> dict:
    try:
        d = json.loads(_STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(_STORE)


def default_level() -> str:
    level = str(_load().get("reasoning_level") or "auto").lower()
    return level if level in LEVELS else "auto"


def set_default_level(level: str) -> dict:
    level = str(level or "").lower()
    if level not in LEVELS:
        raise ValueError("سطح تفکر نامعتبر است (auto/low/medium/high/maximum)")
    with _LOCK:
        d = _load()
        d["reasoning_level"] = level
        _save(d)
    return {"reasoning_level": level}


def variant_ids(model: dict) -> list[str]:
    out = []
    for v in (model or {}).get("variants") or []:
        vid = str((v or {}).get("id") or "")
        if vid and vid not in out:
            out.append(vid)
    return out


def _pick(level: str, ids: list[str]) -> tuple[str | None, bool]:
    if not ids:
        return None, False
    if level == "auto":
        for want in ("medium", "high", "low", "max", "xhigh", "minimal"):
            if want in ids:
                return want, True
        return ids[0], False
    exact_names = {"low": {"low"}, "medium": {"medium"},
                   "high": {"high"}, "maximum": {"max", "maximum"}}
    for want in _PREFER.get(level, ()):
        if want in ids:
            return want, want in exact_names.get(level, set())
    # nearest available: keep honest that it is a fallback
    if level in ("high", "maximum") and ids:
        ranked = [x for x in ids if x in ("max", "xhigh", "high", "medium")]
        if ranked:
            return ranked[0], False
    return ids[-1], False


def resolve(level: str, model: dict, goal: str = "", kind: str = "") -> dict:
    """Map a requested level to a real variant of THIS model, honestly."""
    requested = str(level or "auto").lower()
    if requested not in LEVELS:
        requested = "auto"
    ids = variant_ids(model)
    effective = requested
    if requested == "auto":
        # task-aware auto: bigger tasks get a higher real variant when the
        # model actually offers one.
        complex_task = (kind in ("pipeline", "android") or
                        len(goal or "") > 600 or
                        any(w in (goal or "").lower()
                            for w in ("معماری", "پیچیده", "complex", "refactor")))
        effective = "high" if complex_task else "medium"
    variant, exact = _pick(effective, ids)
    if not variant:
        return {"requested": requested, "effective": effective, "variant": None,
                "supported": False, "exact": False,
                "note": "مدل «%s» هیچ سطح تفکری ارائه نمیدهد؛ سطح درخواستی قابل اعمال نیست."
                        % (model or {}).get("modelID", "?")}
    note = ("سطح «%s» باvariant واقعی «%s» اعمال شد." % (requested, variant))
    if not exact:
        note += (" مدل سطح دقیق «%s» را ندارد؛ نزدیکترین سطح موجود استفاده شد."
                 % requested)
    if requested == "auto":
        note = ("خودکار: سطح «%s» براساس نوع/پیچیدگی مأموریت انتخاب شد؛ "
                "variant واقعی: «%s»." % (effective, variant))
    return {"requested": requested, "effective": effective, "variant": variant,
            "supported": True, "exact": exact, "note": note}


def resolve_for_model(model: str | None, level: str, goal: str = "",
                      kind: str = "") -> dict:
    """Resolve a level against a REAL connected model id (providerID/modelID)."""
    from core.model_gateway import gateway
    mid = str(model or "")
    provider, _, ident = mid.partition("/")
    found = None
    for m in gateway().list_models():
        if provider and m.get("providerID") == provider and m.get("modelID") == ident:
            found = m
            break
    return resolve(level, found or {"providerID": provider, "modelID": ident,
                                    "variants": []}, goal, kind)


def catalog(models: list[dict], goal: str = "", kind: str = "") -> list[dict]:
    """Levels supported by each real model — for the Workspace UI."""
    out = []
    for m in models or []:
        mid = "%s/%s" % (m.get("providerID") or "", m.get("modelID") or "")
        levels = {}
        for level in LEVELS:
            r = resolve(level, m, goal, kind)
            levels[level] = {"supported": r["supported"],
                             "variant": r["variant"], "exact": r["exact"]}
        out.append({"id": mid, "name": m.get("name") or mid,
                    "levels": levels, "variants": variant_ids(m)})
    return out
