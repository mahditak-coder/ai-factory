"""AF NEXT Phase 05 — deterministic domain specialist team activation.

Uses the SINGLE canonical registry from Phase 04. No parallel catalog, no
specialist execution, no provider/model call. Activation is explainable,
least-privilege, and fails closed on ambiguous domains, unknown roles and
dependency cycles.
"""
from __future__ import annotations

import fcntl
import json
import os
import secrets
import threading
from contextlib import contextmanager
from pathlib import Path

import core.config as config
from core import specialist_engine as se, workflow_roles

SCHEMA_VERSION = "af-domain-teams-1.0"
_FLAGS = config.PROJECT_ROOT / "data" / "domain_team_flags.json"

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
    "level-designer", "game-balance-specialist"))
# narrative-designer and music-sound-specialist are CROSS-domain capabilities:
# their canonical product_types intentionally include non-game delivery types.
_CROSS_DOMAIN_CREATIVE = frozenset(("narrative-designer",
                                    "music-sound-specialist"))
# Narrow Finding D repair: these two roles previously fell through to default
# software ownership, so the design/media flags never gated them. Ownership is
# now explicit and authoritative (product-type compatibility is unchanged).
_DESIGN_ROLES = frozenset(("visual-designer",))
_MEDIA_ROLES = frozenset(("audio-designer",)) | _CROSS_DOMAIN_CREATIVE
# Domain-specific ownership is authoritative and derived from the canonical
# contract inventory (every ID here is verified present by reconciliation).
_EDUCATION_ROLES = frozenset(("education-specialist", "curriculum-designer",
                              "learning-experience-specialist"))
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


class FlagStoreError(RuntimeError):
    """Raised when flag storage is corrupt/unreadable; never silently enable."""


@contextmanager
def _flag_lock():
    """Process-safe lock + unique temp file for flag read/modify/write."""
    lock = Path(str(_FLAGS) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o600)
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


def _load_flags_unlocked() -> dict:
    try:
        text = _FLAGS.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}  # genuine first run
    except OSError as exc:
        raise FlagStoreError("flag-store-unreadable:%s" %
                             type(exc).__name__) from exc
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        raise FlagStoreError("flag-store-corrupt-json")
    if not isinstance(data, dict):
        raise FlagStoreError("flag-store-invalid-root")
    for key, value in data.items():
        if not isinstance(key, str) or not key.startswith("domain:") or \
                key[7:] not in DOMAINS or not isinstance(value, bool):
            raise FlagStoreError("flag-store-invalid-entry:" + str(key)[:60])
    return data


def _save_flags_unlocked(data: dict) -> None:
    tmp = Path(str(_FLAGS) + ".%d.%d.%s.tmp" %
               (os.getpid(), threading.get_ident(), secrets.token_hex(6)))
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1,
                                  sort_keys=True), encoding="utf-8")
        tmp.replace(_FLAGS)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def domain_enabled(domain: str) -> bool:
    """Fail closed on flag store errors (never silently enable a domain)."""
    if domain not in DOMAINS:
        return False
    try:
        with _flag_lock():
            data = _load_flags_unlocked()
    except (FlagStoreError, OSError):
        return False
    return data.get("domain:" + domain, True) is True


def set_domain_flag(domain: str, enabled: bool) -> dict:
    if domain not in DOMAINS:
        raise TeamError("unknown-domain-flag:" + domain)
    if not isinstance(enabled, bool):
        raise TeamError("invalid-flag-type:" + type(enabled).__name__)
    with _flag_lock():
        data = _load_flags_unlocked()
        data["domain:" + domain] = enabled
        _save_flags_unlocked(data)
    return {"domain": domain, "enabled": enabled}


def domain_flags() -> dict:
    return {d: domain_enabled(d) for d in DOMAINS}


_ACCEPTED_RISKS = frozenset(("low", "medium", "high", "critical", "unknown"))


def _validate_classification_inputs(product_type, goal, capabilities,
                                    constraints, risk, project_context) -> list:
    """Deterministic validation of all external classification inputs."""
    problems = []
    if product_type is not None and type(product_type) is not str:
        problems.append("invalid-product-type:" + type(product_type).__name__)
    if goal is not None and type(goal) is not str:
        problems.append("invalid-goal:" + type(goal).__name__)
    if capabilities is not None:
        if not isinstance(capabilities, (list, tuple, set)):
            problems.append("invalid-capabilities:" +
                            type(capabilities).__name__)
        else:
            for cap in capabilities:
                if type(cap) is not str:
                    problems.append("invalid-capability:" +
                                    type(cap).__name__)
    if constraints is not None:
        if not isinstance(constraints, (list, tuple)):
            problems.append("invalid-constraints:" +
                            type(constraints).__name__)
        else:
            for value in constraints:
                if type(value) is not str:
                    problems.append("invalid-constraint:" +
                                    type(value).__name__)
    if risk is not None:
        if type(risk) is not str:
            problems.append("invalid-risk:" + type(risk).__name__)
        elif risk.strip() and risk.strip().lower() not in _ACCEPTED_RISKS:
            problems.append("invalid-risk:" + str(risk)[:60])
    if project_context is not None and not isinstance(project_context, dict):
        problems.append("invalid-project-context:" +
                        type(project_context).__name__)
    return problems


def classify_context(product_type: str = "", goal: str = "",
                     capabilities=None, constraints=None, risk: str = "",
                     project_context=None) -> dict:
    """Deterministic domain/platform/capability classification (independent)."""
    input_problems = _validate_classification_inputs(
        product_type, goal, capabilities, constraints, risk, project_context)
    if any(p.startswith("invalid-goal") or p.startswith("invalid-product-type")
           for p in input_problems):
        return {"schema_version": SCHEMA_VERSION, "domain": "unknown",
                "platform": "unknown", "capabilities": [], "constraints": [],
                "risk": "unknown", "risk_invalid": True,
                "project_context": {}, "product_type": "", "goal": "",
                "input_problems": input_problems}
    text = (goal or "").lower()
    ptype = (product_type or "").lower()
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

    caps = {c for c in (capabilities or []) if type(c) is str}
    for cap, keywords in _CAPABILITY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            caps.add(cap)
    risk_raw = risk if type(risk) is str else ""
    risk_value = (risk_raw or "unknown").strip().lower()
    risk_invalid = (type(risk) is not str or
                    risk_value not in _ACCEPTED_RISKS)
    if risk_invalid:
        risk_value = "unknown"  # fail-closed: high-risk roles still activated
    return {"schema_version": SCHEMA_VERSION, "domain": domain,
            "platform": platform, "capabilities": sorted(caps),
            "constraints": [c for c in (constraints or [])
                            if type(c) is str],
            "risk": risk_value, "risk_invalid": risk_invalid,
            "project_context": (dict(project_context)
                                if isinstance(project_context, dict) else {}),
            "product_type": ptype, "goal": str(goal or ""),
            "input_problems": input_problems}


def _eligible_roles(ctx: dict) -> tuple[set[str], list[str]]:
    """Select only roles the request actually needs and is allowed to use."""
    domain, platform = ctx["domain"], ctx["platform"]
    caps = set(ctx["capabilities"])
    if domain in ("ambiguous", "unknown"):
        return set(), ["essential-clarification-required:" + domain]
    if not domain_enabled(domain):
        return set(), ["domain-disabled:" + domain]
    registry = se.canonical_registry()

    def usable(role_id):
        return (role_id in registry and
                _role_eligible(role_id, ctx, registry) and
                _owner_enabled(role_id, registry))

    selected = {r for r in _CORE_BY_DOMAIN.get(domain, ()) if usable(r)}
    if platform == "Android" and domain_enabled("android"):
        selected.update(r for r in _ANDROID_ROLES if usable(r))
    elif domain == "game" and "mobile" in caps and domain_enabled("android"):
        selected.update(r for r in _ANDROID_ROLES if usable(r))
    if domain == "game":
        selected.update(r for r in ("audio-game", "security-game") if usable(r))
    if ctx.get("risk") in ("high", "critical") or ctx.get("risk_invalid"):
        sec = se.resolve_id("security-game" if domain == "game"
                            else "security-reviewer")
        if usable(sec):
            selected.add(sec)
    if "release" in caps:
        selected.update(r for r in ("release-engineer",) if usable(r))
    if domain == "education":
        selected.update(r for r in ("accessibility-specialist",) if usable(r))

    for role_id, cap in _CAPABILITY_ROLES.items():
        if cap in caps and usable(role_id):
            selected.add(role_id)
    if domain != "game":
        selected -= _GAME_ONLY
    unknown = sorted(r for r in selected if r not in registry)
    if unknown:
        return set(), ["unknown-role:" + unknown[0]]
    return selected, []


def _role_domain(role_id: str, registry: dict | None = None) -> str:
    """Authoritative ownership of a role, from Phase 05 domain contract sets.

    These sets are reconciled against the single canonical registry so every
    domain-specific role is owned exactly once. Generic roles (including those
    with ``*`` product types) are owned by the software domain.
    """
    rid = se.resolve_id(role_id)
    if rid in _GAME_ONLY:
        return "game"
    if rid in _EDUCATION_ROLES:
        return "education"
    if rid in _ANDROID_ROLES:
        return "android"
    if rid in _DESIGN_ROLES:
        return "design"
    if rid in _MEDIA_ROLES:
        return "media"
    return "software"


def _owner_enabled(role_id: str, registry: dict) -> bool:
    """True only when the role's authoritative owner domain flag is enabled."""
    return domain_enabled(_role_domain(role_id, registry))


def _types_compatible(contract: dict, ctx: dict) -> bool:
    """True when a contract's canonical product_types allow this request."""
    types = set(contract["product_types"])
    if "*" in types:
        return True
    ptype, platform, domain = (ctx["product_type"], ctx["platform"],
                               ctx["domain"])
    if ptype in types:
        return True
    role_domains = {_TYPE_DOMAIN.get(t, t) for t in types}
    if _TYPE_DOMAIN.get(ptype) in role_domains:
        return True
    # Delivery-platform compatibility (game is NOT synonymous with Android).
    platform_types = {t for t, plat in _TYPE_PLATFORM.items()
                      if plat == platform}
    if platform_types & types:
        return True
    return domain in role_domains


def _role_eligible(role_id: str, ctx: dict, registry: dict) -> bool:
    """Domain ownership + canonical product-type compatibility gate."""
    rid = se.resolve_id(role_id)
    contract = registry.get(rid)
    if contract is None:
        return False
    domain, platform = ctx["domain"], ctx["platform"]
    # Authoritative domain ownership gates: a domain-specific role can only
    # belong to its own domain; capability keywords never bypass this.
    if rid in _GAME_ONLY:
        return domain == "game"
    if rid in _EDUCATION_ROLES:
        return domain == "education"
    if rid in _ANDROID_ROLES:
        return platform == "Android" or domain == "android"
    if rid in _CROSS_DOMAIN_CREATIVE:
        return _types_compatible(contract, ctx)
    types = set(contract["product_types"])
    if "*" in types:
        return True
    return _types_compatible(contract, ctx)


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
    blocking = [p for p in ctx.get("input_problems", [])
                if not p.startswith("invalid-risk")]
    if blocking:
        return {"ok": False, "context": ctx, "team": [], "questions": [],
                "problems": blocking}
    registry = se.canonical_registry()
    try:
        flag_off = (ctx["domain"] not in ("ambiguous", "unknown") and
                    not domain_enabled(ctx["domain"]))
    except (FlagStoreError, OSError):
        flag_off = True  # corrupt flag storage -> documented Phase 04 fallback
    if flag_off:
        # Feature flag OFF / flag store unavailable: fall back to the accepted
        # Phase 04 read-only composition. No data is deleted and no unsafe
        # execution path is activated; this module only selects roles.
        legacy = se.compose_team(ctx["product_type"], goal or "")
        return {"ok": True, "context": ctx, "team": legacy,
                "mode": "phase04", "problems": [], "questions": [],
                "hash": hash_from(legacy)}
    selected, problems = _eligible_roles(ctx)
    selected = {se.resolve_id(r) for r in selected}
    if problems:
        return {"ok": False, "context": ctx, "team": [], "problems": problems,
                "questions": problems}

    # Dependency closure inside the canonical registry only. Eligibility is
    # enforced twice: once when roles are selected, and again here so a
    # disabled secondary domain can never leak in through dependency expansion.
    # The algorithm is monotonic (add, then remove only) so it always
    # terminates even when cycles or ineligible dependencies are present.
    def base_eligible(rid):
        return (rid in registry and
                _role_eligible(rid, ctx, registry) and
                _owner_enabled(rid, registry))

    queue = list(selected)
    while queue:
        rid = queue.pop()
        contract = registry.get(rid)
        if contract is None:
            return {"ok": False, "context": ctx, "team": [], "questions": [],
                    "problems": ["unknown-role:" + rid]}
        for dep in contract["depends_on"]:
            dep = se.resolve_id(dep)
            if dep not in registry:
                return {"ok": False, "context": ctx, "team": [],
                        "questions": [],
                        "problems": ["unresolved-dependency:" + rid +
                                    ":" + dep]}
            if base_eligible(dep) and dep not in selected:
                selected.add(dep)
                queue.append(dep)

    changed = True
    while changed:
        changed = False
        for rid in list(selected):
            if not base_eligible(rid):
                selected.discard(rid)
                changed = True
                continue
            for dep in registry[rid]["depends_on"]:
                if se.resolve_id(dep) not in selected:
                    selected.discard(rid)
                    changed = True
                    break
    if not selected:
        return {"ok": False, "context": ctx, "team": [], "questions": [],
                "problems": ["no-eligible-roles"]}

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
        for field in ("expected_artifacts", "inputs", "dependencies",
                      "acceptance"):
            if not isinstance(item.get(field), list):
                problems.append("malformed-field:%s:%s" % (rid, field))
        if "title" in item and item["title"] != contract["title"]:
            problems.append("forged-title:" + rid)
        if "domain" in item and item["domain"] != _role_domain(rid, registry):
            problems.append("forged-domain:" + rid)
        if "stage_id" in item and (type(item["stage_id"]) is not str or
                                   not item["stage_id"].startswith(
                                       ("s", "role:"))):
            problems.append("forged-stage:" + rid)
        position = ids.index(rid)
        for dep in contract["depends_on"]:
            dep = se.resolve_id(dep)
            if dep in ids and ids.index(dep) > position:
                problems.append("dependency-order:" + rid + ":" + dep)
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
        # allowed_scopes must match the canonical contract EXACTLY: same
        # members, complete, no duplicates and the same canonical ordering.
        canonical_scopes = list(contract["allowed_scopes"])
        scopes = item.get("allowed_scopes")
        if not isinstance(scopes, list):
            problems.append("malformed-scopes:" + rid)
        elif not scopes:
            problems.append("missing-scopes:" + rid)
        elif not all(type(scope) is str for scope in scopes):
            # Unhashable or non-string values would raise TypeError inside
            # set()/membership checks. Fail closed before any set operation.
            bad_index = next(i for i, scope in enumerate(scopes)
                             if type(scope) is not str)
            problems.append("malformed-scope:%s:%d" % (rid, bad_index))
        else:
            if len(scopes) != len(set(scopes)):
                problems.append("duplicate-scope:" + rid)
            allowed = set(canonical_scopes)
            for scope in scopes:
                if scope not in allowed:
                    problems.append("unauthorized-scope:" + rid + ":" +
                                    str(scope))
            for scope in canonical_scopes:
                if scope not in scopes:
                    problems.append("omitted-scope:" + rid + ":" + str(scope))
            if (scopes != canonical_scopes and len(scopes) ==
                    len(canonical_scopes) and set(scopes) == allowed and
                    len(scopes) == len(set(scopes))):
                problems.append("scope-order-mismatch:" + rid)
        for dep in canonical_deps:
            if dep not in ids:
                problems.append("unresolved-dependency:" + rid + ":" + dep)
    return {"ok": not problems, "problems": problems}
