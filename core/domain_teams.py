"""AF NEXT Phase 05 — deterministic domain specialist team activation.

Uses the SINGLE canonical registry from Phase 04. No parallel catalog, no
specialist execution, no provider/model call. Activation is explainable,
least-privilege, and fails closed on ambiguous domains, unknown roles and
dependency cycles.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import core.config as config
from core import specialist_engine as se, workflow_roles

SCHEMA_VERSION = "af-domain-teams-1.0"
_FLAGS = config.PROJECT_ROOT / "data" / "domain_team_flags.json"
_LOCK = threading.RLock()

DOMAINS = ("software", "android", "game", "education", "design", "media")
PLATFORMS = ("Android", "Web", "Desktop", "unknown")

_DOMAIN_KEYWORDS = {
    "game": ("بازی", "game", "level", "player", "gdd", "مرحله"),
    "education": ("آموزش", "education", "curriculum", "learning", "درس",
                  "یادگیری"),
    "software": ("نرم افزار", "software", "web", "api", "سایت",
                 "backend", "frontend", "database", "وب"),
    "design": ("طراحی", "design", "ui", "ux", "visual", "رابط"),
    "media": ("صدا", "audio", "music", "تصویر", "image", "موسیقی"),
}
_PLATFORM_KEYWORDS = {
    "Android": ("اندروید", "android", "apk", "aab"),
    "Web": ("وب", "web", "سایت", "html", "browser"),
    "Desktop": ("دسکتاپ", "desktop", "windows", "linux"),
}
_CAPABILITY_KEYWORDS = {
    "backend": ("backend", "بکاند", "سرور"),
    "frontend": ("frontend", "فراند", "رابط کاربری"),
    "database": ("database", "دیتابیس", "db"),
    "api": ("api", "ای پی آی"),
    "devops": ("devops", "pipeline", "خط لوله"),
    "mobile": ("mobile", "موبایل", "اندروید", "android"),
    "accessibility": ("accessibility", "دسترسپذیری"),
    "visual": ("visual", "بصری", "طراحی"),
    "audio": ("audio", "صدا", "موسیقی"),
    "music": ("music", "موسیقی"),
    "level": ("level", "مرحله"),
    "balance": ("balance", "تعادل"),
    "narrative": ("narrative", "داستان", "روایت"),
    "curriculum": ("curriculum", "برنامه درسی"),
    "learning": ("learning", "یادگیری"),
    "security": ("security", "امنیت"),
    "release": ("release", "انتشار", "deploy"),
    "ai": ("ai", "هوش مصنوعی"),
}
_TYPE_DOMAIN = {
    "game": "game", "education": "education", "android": "software",
    "webapp": "software", "webpage": "software", "api": "software",
    "fullstack": "software", "pwa": "software", "ai-tool": "software",
    "shop": "software", "dashboard": "software", "webtool": "software",
    "wordpress": "software", "php": "software", "node-server": "software",
}
_TYPE_PLATFORM = {
    "android": "Android", "webapp": "Web", "webpage": "Web", "api": "Web",
    "pwa": "Web", "fullstack": "Web", "shop": "Web", "dashboard": "Web",
    "webtool": "Web", "wordpress": "Web", "php": "Web", "node-server": "Web",
}

# Roles activated only by explicit capability evidence (never by default).
_CAPABILITY_ROLES = {
    "backend-specialist": "backend", "frontend-specialist": "frontend",
    "database-specialist": "database", "api-specialist": "api",
    "devops-specialist": "devops", "application-architect": "mobile",
    "level-designer": "level", "game-balance-specialist": "balance",
    "narrative-designer": "narrative", "curriculum-designer": "curriculum",
    "learning-experience-specialist": "learning",
    "accessibility-specialist": "accessibility", "visual-designer": "visual",
    "music-sound-specialist": "music", "ai-engineer": "ai",
}
# Roles that must never be activated for a simple web/document task.
_GAME_ONLY = frozenset((
    "game-designer", "game-designer-gdd", "game-developer", "art-director",
    "character-artist", "environment-artist", "ui-game", "animator",
    "audio-game", "technical-artist-25d", "qa-game", "security-game",
    "level-designer", "game-balance-specialist", "narrative-designer",
    "music-sound-specialist"))
_ANDROID_ROLES = frozenset(("android-developer", "mobile-ux-specialist"))
_CORE_BY_DOMAIN = {
    "software": ("product-architect", "software-architect", "ui-ux-designer",
                 "developer", "qa-engineer"),
    "game": ("product-director", "game-designer-gdd", "art-director", "ui-game",
             "game-developer", "qa-game"),
    "education": ("product-architect", "education-specialist", "ui-ux-designer",
                  "developer", "qa-engineer"),
    "android": ("product-architect", "software-architect", "ui-ux-designer",
                "developer", "qa-engineer"),
    "design": ("product-architect", "ui-ux-designer", "visual-designer"),
    "media": ("product-architect", "audio-designer"),
}


class TeamError(RuntimeError):
    pass


def _load_flags() -> dict:
    try:
        data = json.loads(_FLAGS.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_flags(data: dict) -> None:
    _FLAGS.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(_FLAGS) + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1,
                              sort_keys=True), encoding="utf-8")
    tmp.replace(_FLAGS)


def domain_enabled(domain: str) -> bool:
    return bool(_load_flags().get("domain:" + str(domain), True))


def set_domain_flag(domain: str, enabled: bool) -> dict:
    if domain not in DOMAINS:
        raise TeamError("unknown-domain-flag:" + domain)
    with _LOCK:
        data = _load_flags()
        data["domain:" + domain] = bool(enabled)
        _save_flags(data)
    return {"domain": domain, "enabled": bool(enabled)}


def domain_flags() -> dict:
    return {d: domain_enabled(d) for d in DOMAINS}


def classify_context(product_type: str = "", goal: str = "",
                     capabilities=None, constraints=None, risk: str = "",
                     project_context=None) -> dict:
    """Deterministic domain/platform/capability classification (independent)."""
    text = str(goal or "").lower()
    ptype = str(product_type or "").lower()
    scores = {d: 3 * sum(1 for kw in kws if kw in text)
              for d, kws in _DOMAIN_KEYWORDS.items()}
    if ptype in _TYPE_DOMAIN:
        scores[_TYPE_DOMAIN[ptype]] += 2
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    if ranked[0][1] == 0:
        domain = "unknown"
    elif len(ranked) > 1 and ranked[0][1] == ranked[1][1] and ranked[0][1] > 0:
        domain = "ambiguous"
    else:
        domain = ranked[0][0]

    platform_scores = {p: sum(1 for kw in kws if kw in text)
                       for p, kws in _PLATFORM_KEYWORDS.items()}
    if ptype in _TYPE_PLATFORM:
        platform_scores[_TYPE_PLATFORM[ptype]] += 2
    best = sorted(platform_scores.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    platform = best[0] if best[1] else "unknown"

    caps = set(capabilities or [])
    for cap, keywords in _CAPABILITY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            caps.add(cap)
    return {"schema_version": SCHEMA_VERSION, "domain": domain,
            "platform": platform, "capabilities": sorted(caps),
            "constraints": list(constraints or []),
            "risk": str(risk or "unknown"),
            "project_context": project_context or {},
            "product_type": ptype, "goal": str(goal or "")}


def _eligible_roles(ctx: dict) -> tuple[set[str], list[str]]:
    """Select only roles the request actually needs."""
    domain, platform = ctx["domain"], ctx["platform"]
    caps = set(ctx["capabilities"])
    if domain in ("ambiguous", "unknown"):
        return set(), ["essential-clarification-required:" + domain]
    if not domain_enabled(domain):
        return set(), ["domain-disabled:" + domain]

    selected = set(_CORE_BY_DOMAIN.get(domain, ()))
    if platform == "Android":
        selected.update(_ANDROID_ROLES)
    elif domain == "game" and "mobile" in caps:
        selected.update(_ANDROID_ROLES)
    if domain == "game":
        selected.add("audio-game")
        selected.add("security-game")
    if ctx.get("risk") in ("high", "critical"):
        selected.add("security-game" if domain == "game" else "security-reviewer")
    if "release" in caps:
        selected.add("release-engineer")
    if domain == "education":
        selected.add("accessibility-specialist")

    registry = se.canonical_registry()
    for role_id, cap in _CAPABILITY_ROLES.items():
        if cap in caps and role_id in registry and domain_enabled(
                _role_domain(role_id)):
            selected.add(role_id)
    # A game-only role can never be selected for a non-game domain.
    if domain != "game":
        selected -= _GAME_ONLY
    # Roles must exist in the single canonical registry.
    unknown = sorted(r for r in selected if r not in registry)
    if unknown:
        return set(), ["unknown-role:" + unknown[0]]
    return selected, []


def _role_domain(role_id: str) -> str:
    if role_id in _GAME_ONLY:
        return "game"
    if role_id in _ANDROID_ROLES:
        return "android"
    if role_id in ("education-specialist", "curriculum-designer",
                   "learning-experience-specialist"):
        return "education"
    if role_id in ("backend-specialist", "frontend-specialist",
                   "database-specialist", "api-specialist", "devops-specialist",
                   "application-architect", "qa-engineer", "developer",
                   "software-architect", "ai-engineer"):
        return "software"
    if role_id in ("music-sound-specialist", "visual-designer"):
        return "media"
    return "software"


def _detect_cycle(selected: set[str], registry: dict) -> list[str]:
    color: dict[str, int] = {}

    def visit(node: str, stack: tuple) -> list[str]:
        if color.get(node) == 2:
            return []
        if color.get(node) == 1:
            return list(stack) + [node]
        color[node] = 1
        for dep in registry[node]["depends_on"]:
            dep = se.resolve_id(dep)
            if dep in selected:
                cycle = visit(dep, stack + (node,))
                if cycle:
                    return cycle
        color[node] = 2
        return []

    for rid in sorted(selected):
        cycle = visit(rid, ())
        if cycle:
            return cycle
    return []


def _topological_order(selected: set, registry: dict, dag: list) -> list:
    """Kahn ordering over ONLY selected roles and canonical dependencies.

    Aliases are resolved before comparison. Ties are broken by historical DAG
    stage order, then canonical role ID, so the result is deterministic. The
    DAG itself is not modified and no extra role is activated for ordering.
    """
    nodes = {se.resolve_id(r) for r in selected}
    deps = {}
    for rid in nodes:
        contract = registry.get(rid)
        if contract is None:
            raise TeamError("unknown-role:" + rid)
        canonical = []
        for dep in contract["depends_on"]:
            dep = se.resolve_id(dep)
            if dep not in registry:
                raise TeamError("unresolved-dependency:" + rid + ":" + dep)
            canonical.append(dep)
        deps[rid] = canonical
    rank = {}
    for idx, stage in enumerate(dag):
        sid = se.resolve_id(stage.get("role"))
        if sid not in rank:
            rank[sid] = idx
    def sort_key(rid):
        return (rank.get(rid, 10 ** 6), rid)
    remaining = set(nodes)
    ordered = []
    while remaining:
        ready = sorted((rid for rid in remaining
                        if all(d in ordered for d in deps[rid])),
                       key=sort_key)
        if not ready:
            raise TeamError("dependency-cycle:" +
                            "->".join(sorted(remaining)))
        ordered.append(ready[0])
        remaining.remove(ready[0])
    return ordered


def build_team(product_type: str = "", goal: str = "",
               capabilities=None, constraints=None, risk: str = "",
               project_context=None) -> dict:
    """Compose an ordered, bounded, dependency-complete team. Never executes."""
    ctx = classify_context(product_type, goal, capabilities, constraints,
                           risk, project_context)
    registry = se.canonical_registry()
    if ctx["domain"] not in ("ambiguous", "unknown") and not domain_enabled(
            ctx["domain"]):
        # Feature flag OFF: fall back to the accepted Phase 04 read-only
        # composition. No data is deleted and no unsafe legacy execution path
        # is activated — this module only selects roles, never executes them.
        legacy = se.compose_team(ctx["product_type"], goal or "")
        return {"ok": True, "context": ctx, "team": legacy, "mode": "phase04",
                "problems": [], "questions": [],
                "hash": hash_from(legacy)}
    selected, problems = _eligible_roles(ctx)
    selected = {se.resolve_id(r) for r in selected}
    if problems:
        return {"ok": False, "context": ctx, "team": [], "problems": problems,
                "questions": problems}

    # Resolve dependencies inside the canonical registry only.
    queue = list(selected)
    while queue:
        rid = queue.pop()
        contract = registry.get(rid)
        if contract is None:
            return {"ok": False, "context": ctx, "team": [],
                    "problems": ["unknown-role:" + rid], "questions": []}
        for dep in contract["depends_on"]:
            dep = se.resolve_id(dep)
            if dep not in registry:
                return {"ok": False, "context": ctx, "team": [], "questions": [],
                        "problems": ["unresolved-dependency:" + rid + ":" + dep]}
            if dep not in selected:
                selected.add(dep)
                queue.append(dep)

    cycle = _detect_cycle(selected, registry)
    if cycle:
        return {"ok": False, "context": ctx, "team": [], "questions": [],
                "problems": ["dependency-cycle:" + "->".join(cycle)]}

    dag = workflow_roles.dag_for(ctx["product_type"], goal or "")
    stage_by_role = {}
    for stage in dag:
        sid = se.resolve_id(stage.get("role"))
        if sid not in stage_by_role:
            stage_by_role[sid] = stage.get("id")
    try:
        ordered = _topological_order(selected, registry, dag)
    except TeamError as exc:
        return {"ok": False, "context": ctx, "team": [], "questions": [],
                "problems": [str(exc)]}
    team = []
    for rid in ordered:
        contract = registry[rid]
        team.append({"role_id": rid,
                     "stage_id": stage_by_role.get(rid, "role:" + rid),
                     "title": contract["title"],
                     "domain": _role_domain(rid),
                     "dependencies": [se.resolve_id(d)
                                      for d in contract["depends_on"]],
                     "inputs": list(contract["inputs"]),
                     "expected_artifacts": list(contract["artifacts"]),
                     "allowed_scopes": list(contract["allowed_scopes"]),
                     "acceptance": list(contract["acceptance"])})
    report = validate_team(team)
    return {"ok": report["ok"], "context": ctx, "team": team,
            "problems": report["problems"], "questions": [],
            "hash": hash_from(team)}


def hash_from(team: list) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(team, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()


def validate_team(team: list) -> dict:
    """Validate every sensitive field against the canonical contract."""
    problems = []
    if not isinstance(team, list):
        return {"ok": False, "problems": ["team-not-list"]}
    ids = []
    for item in team:
        ids.append(se.resolve_id(item.get("role_id")) if isinstance(item, dict)
                   else "")
    if len(ids) != len(set(ids)):
        problems.append("duplicate-role")
    registry = se.canonical_registry()
    for item, rid in zip(team, ids):
        if not isinstance(item, dict):
            problems.append("team-item-not-dict")
            continue
        contract = registry.get(rid)
        if contract is None:
            problems.append("unknown-role:" + str(item.get("role_id")))
            continue
        # Artifacts must be EXACTLY the canonical artifact schemas.
        expected = item.get("expected_artifacts")
        if not expected:
            problems.append("missing-artifacts:" + rid)
        elif expected != contract["artifacts"]:
            problems.append("artifact-mismatch:" + rid)
        if item.get("inputs") != contract["inputs"]:
            problems.append("input-mismatch:" + rid)
        canonical_deps = [se.resolve_id(d) for d in contract["depends_on"]]
        if item.get("dependencies") != canonical_deps:
            problems.append("dependency-mismatch:" + rid)
        if item.get("acceptance") != contract["acceptance"]:
            problems.append("acceptance-mismatch:" + rid)
        scopes = item.get("allowed_scopes")
        allowed = set(contract["allowed_scopes"])
        if not isinstance(scopes, list) or not scopes:
            problems.append("missing-scopes:" + rid)
        else:
            for scope in scopes:
                if scope not in allowed:
                    problems.append("unauthorized-scope:" + rid + ":" +
                                    str(scope))
        for dep in canonical_deps:
            if dep not in ids:
                problems.append("unresolved-dependency:" + rid + ":" + dep)
    return {"ok": not problems, "problems": problems}
