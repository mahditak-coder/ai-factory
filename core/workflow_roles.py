"""AF NEXT — product-type aware specialist DAGs for the human-gated workflow.

Additive: reuses existing Specialist contracts; only adds orchestration data.
"""
from __future__ import annotations


def stage(sid, role, title, deps=(), kind="document", tool=None):
    return {"id": sid, "role": role, "title": title, "deps": list(deps),
            "kind": kind, "tool": tool}


GAME = [
    stage("s01-product", "product-director", "Product Director — سند محصول", (), "document"),
    stage("s02-gdd", "game-designer-gdd", "Game Designer — مکانیک و جریان", ("s01-product",), "document"),
    stage("s03-art", "art-director", "Art Director — سبک و نور 2.5D", ("s01-product",), "document"),
    stage("s04-character", "character-artist", "Character Artist — Character Bible",
          ("s03-art",), "image", "svg"),
    stage("s05-environment", "environment-artist", "Environment Artist — نقشه صحنه",
          ("s03-art",), "image", "svg"),
    stage("s06-ui", "ui-game", "UI/UX Designer — نمونه صفحات",
          ("s01-product", "s03-art"), "page"),
    stage("s07-tech25d", "technical-artist-25d", "2.5D Technical Artist — لایهها و دوربین",
          ("s05-environment", "s06-ui"), "diagram"),
    stage("s08-anim", "animator", "Animator — حالتها و انیمیشن",
          ("s04-character", "s07-tech25d"), "document"),
    stage("s09-audio", "audio-game", "Audio Designer — صدا و موسیقی",
          ("s02-gdd",), "audio", "wav"),
    stage("s10-dev", "game-developer", "Game Developer — پیادهسازی",
          ("s08-anim", "s09-audio", "s06-ui"), "code"),
    stage("s11-qa", "qa-game", "QA Engineer — تست مستقل", ("s10-dev",), "document"),
    stage("s12-sec", "security-game", "Security Reviewer — ممیزی", ("s10-dev",), "document"),
    stage("s13-release", "release-engineer", "Release Engineer — ساخت و بستهبندی",
          ("s11-qa", "s12-sec"), "build"),
]

WEB = [
    stage("s01-product", "product-architect", "Product Architect", (), "document"),
    stage("s02-arch", "software-architect", "Software Architect", ("s01-product",), "document"),
    stage("s03-ui", "ui-ux-designer", "UI/UX Designer", ("s01-product",), "page"),
    stage("s04-dev", "developer", "Developer", ("s02-arch", "s03-ui"), "code"),
    stage("s05-qa", "qa-engineer", "QA Engineer", ("s04-dev",), "document"),
    stage("s06-sec", "security-reviewer", "Security Reviewer", ("s04-dev",), "document"),
    stage("s07-release", "release-engineer", "Release Engineer", ("s05-qa", "s06-sec"), "build"),
]

ANDROID = [
    stage("s01-product", "product-architect", "Product Architect", (), "document"),
    stage("s02-arch", "software-architect", "Software Architect", ("s01-product",), "document"),
    stage("s03-ui", "ui-ux-designer", "UI/UX Designer", ("s01-product",), "page"),
    stage("s04-dev", "android-developer", "Android Developer", ("s02-arch", "s03-ui"), "code"),
    stage("s05-qa", "qa-engineer", "QA Engineer", ("s04-dev",), "document"),
    stage("s06-sec", "security-reviewer", "Security Reviewer", ("s04-dev",), "document"),
    stage("s07-release", "release-engineer", "Release Engineer", ("s05-qa", "s06-sec"), "build"),
]

AI = [
    stage("s01-product", "product-architect", "Product Architect", (), "document"),
    stage("s02-arch", "software-architect", "Software Architect", ("s01-product",), "document"),
    stage("s03-ai", "ai-engineer", "AI Engineer", ("s02-arch",), "code"),
    stage("s04-ui", "ui-ux-designer", "UI/UX Designer", ("s01-product",), "page"),
    stage("s05-qa", "qa-engineer", "QA Engineer", ("s03-ai", "s04-ui"), "document"),
    stage("s06-sec", "security-reviewer", "Security Reviewer", ("s03-ai",), "document"),
    stage("s07-release", "release-engineer", "Release Engineer", ("s05-qa", "s06-sec"), "build"),
]

_DAGS = {"game": GAME, "webapp": WEB, "webpage": WEB, "shop": WEB,
         "dashboard": WEB, "saas": WEB, "webtool": WEB, "pwa": WEB,
         "android": ANDROID, "ai-tool": AI, "api": WEB, "fullstack": WEB}


def dag_for(product_type: str, goal: str = "") -> list[dict]:
    """Game DAG is used for game goals even when stored type is generic."""
    g = (goal or "").lower()
    if "game" in g or "بازی" in g or "godot" in g or "3d" in g or "2.5d" in g:
        return GAME
    return [dict(s) for s in _DAGS.get((product_type or "").lower(), WEB)]


def stage_ids(dag: list[dict]) -> list[str]:
    return [s["id"] for s in dag]
