"""AF NEXT Phase 4 — capability-aware engine selection, not tool opportunism."""
from __future__ import annotations

from core import engine_catalog, engine_discovery


def _env_state(profile: engine_catalog.EngineProfile) -> tuple[str, list[str]]:
    d = engine_discovery.report()
    missing = [t for t in profile.tools
               if t in d["tools"] and d["tools"][t]["status"] not in
               (engine_discovery.AVAILABLE,)]
    if profile.status == engine_discovery.BLOCKED:
        return engine_discovery.BLOCKED, [str(profile.limits[0])
                                          if profile.limits else "blocked"]
    if missing:
        return engine_discovery.BLOCKED, missing
    return engine_discovery.AVAILABLE, []


def select(product_type: str, goal: str = "", quality: str = "") -> list[dict]:
    """Rank engines by product fit + real environment capability + quality."""
    q = (goal + " " + quality).lower()
    # A game-specific goal must be answered by the GAME engine, even when that
    # engine is BLOCKED — never by claiming an unrelated AVAILABLE engine can
    # silently satisfy a 3D/native requirement.
    game_intent = any(w in q for w in ("game", "godot", "unity", "3d", "2d",
                                       "بازی", "گیم", "سهبعدی"))
    out = []
    for p in engine_catalog.for_product(product_type):
        capability, blockers = _env_state(p)
        if p.id == "game" and game_intent:
            score = 200
        elif p.id in ("android", "backend_api", "web_saas", "ai_agent"):
            score = 100
        else:
            score = 50
        notes = []
        if any(w in q for w in ("3d", "three-dimensional", "سهبعدی")) and p.id == "game":
            capability = engine_discovery.BLOCKED
            blockers.append("3D requires Godot/Unity: unavailable")
            notes.append("2D HTML5 contract does NOT satisfy a 3D requirement")
        if p.id == "game" and any(w in q for w in ("godot", "unity")):
            notes.append("requested native game engine unavailable — no install performed")
        out.append({"engine": p.id, "title": p.title, "score": score,
                    "status": capability, "blockers": blockers,
                    "capabilities": list(p.capabilities),
                    "limits": list(p.limits), "notes": notes,
                    "installation": "not attempted; requires explicit user approval"
                                    if capability == engine_discovery.BLOCKED else "none"})
    out.sort(key=lambda x: (x["score"],
                            x["status"] == engine_discovery.AVAILABLE),
             reverse=True)
    return out


def best(product_type: str, goal: str = "", quality: str = "") -> dict | None:
    ranked = select(product_type, goal, quality)
    return ranked[0] if ranked else None
