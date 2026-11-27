"""AF NEXT Phase 4 — engine contracts for all six product engines.

A contract exists for every engine; a REAL executable adapter exists only
where the environment actually supports it. Blocked engines say why.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.engine_discovery import AVAILABLE, BLOCKED, NOT_VERIFIED


@dataclass
class EngineProfile:
    id: str
    title: str
    product_types: tuple[str, ...]
    capabilities: tuple[str, ...]
    limits: tuple[str, ...]
    tools: tuple[str, ...]
    structure: str
    build: str
    preview: str
    test: str
    output: str
    success: str
    status: str = AVAILABLE
    adapter: str = ""


PROFILES: tuple[EngineProfile, ...] = (
    EngineProfile("android", "Android (minimal toolchain)", ("android",),
        ("APK", "AAB", "real signing", "asset injection", "self-repair"),
        ("no Gradle/AGP", "no AndroidX/native libs", "no real device install in MIUI"),
        ("java", "javac", "aapt2", "d8", "apksigner", "keytool", "android.jar", "bundletool"),
        "AndroidManifest.xml + java/ + res/ + assets/",
        "aapt2→javac→d8→assets→ziputil→apksigner", "runner preview (limited)",
        "engine.validate + verify_artifacts + project tests",
        "signed APK + release.json + MANIFEST.sha256", "apksigner verify + badging + manifest",
        adapter="android"),
    EngineProfile("web_saas", "Web / SaaS", (
        "webpage", "webapp", "shop", "dashboard", "webtool", "pwa", "fullstack"),
        ("HTML/CSS/JS", "Python stdlib backend", "SQLite", "real HTTP contract", "ZIP"),
        ("npm-based frameworks not in the mainline", "PWA offline not fully verified"),
        ("python3", "node", "chromium-browser", "chromedriver"),
        "index.html + app.js + styles.css (+ server.py for fullstack)",
        "no compile; static/fullstack server", "runner.start + preview port",
        "factory.testrun_combined + real HTTP probes",
        "ZIP bundle", "ran=True and ok=True with real evidence",
        adapter="web_saas"),
    EngineProfile("backend_api", "Backend / API", ("api", "fullstack-python", "node-server"),
        ("real HTTP endpoints", "SQLite persistence", "contract tests"),
        ("no external DB by default", "native packages not installed"),
        ("python3", "node", "curl"),
        "server.py + app/ + tests/test_api.py",
        "no compile; stdlib HTTP", "runner.start + /api/health",
        "typetests.check_api + own unittest suite",
        "runnable server + ZIP", "health 200 + contract PASS",
        adapter="backend_api"),
    EngineProfile("ai_agent", "AI / Agent product", ("ai-tool",),
        ("agent module", "local config", "real provider calls via ModelGateway"),
        ("provider quota external", "no embedded API keys"),
        ("python3", "opencode (via ModelGateway)"),
        "app/ai/ + ui/ai.html + config.json",
        "no compile", "runner.start + /ai.html",
        "typetests + real provider connection test",
        "ZIP + runnable module", "config starts disabled + real endpoint responds",
        adapter="ai_agent"),
    EngineProfile("game", "Game (2D real / 3D blocked)", (
        "webapp", "webpage", "android"),
        ("2D HTML5 Canvas game", "headless Chromium DOM QA", "persistence via local storage"),
        ("Godot/Unity not installed; 3D and native exports BLOCKED",),
        ("node", "chromium-browser", "chromedriver"),
        "index.html + game.js + assets/ + tests/",
        "no compile for HTML5; native game exports unavailable",
        "chromium headless render", "DOM + functional scenario + tool events",
        "web bundle (native game package NOT VERIFIED)",
        "real DOM render + functional test PASS", adapter="game"),
    EngineProfile("desktop", "Desktop native", ("desktop",),
        ("source + runnable Python bundle",),
        ("exe/AppImage/dmg cross-build unavailable on aarch64 Termux",),
        ("python3",),
        "server.py + run.sh + README",
        "no native compile", "local Python process",
        "python tests", "source bundle + run instructions",
        "real process starts; native installer NOT VERIFIED",
        status=BLOCKED, adapter="desktop"),
)

_BY_ID = {p.id: p for p in PROFILES}


def get(engine_id: str) -> EngineProfile | None:
    return _BY_ID.get(engine_id)


def for_product(product_type: str) -> list[EngineProfile]:
    return [p for p in PROFILES if product_type in p.product_types]
