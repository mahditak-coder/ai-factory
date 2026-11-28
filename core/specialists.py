"""AF NEXT Phase 3 — independent Specialist Agent contracts.

Each role has explicit inputs, responsibilities, allowed tool scopes, REAL
artifact paths and its own acceptance criteria.  No new model engine is added:
execution goes through ModelGateway, tools through ToolRegistry/ToolExecutor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ArtifactSpec:
    path: str                    # relative to projects/<project>/
    format: str                  # md | html | json | py
    required_sections: tuple[str, ...] = ()

    def validate(self, content: str) -> str | None:
        if not content or not content.strip():
            return "خروجی خالی است"
        for section in self.required_sections:
            if section not in content:
                return "بخش اجباریmissing: " + section
        if self.format == "json":
            import json
            try:
                json.loads(content)
            except ValueError as exc:
                return "JSON نامعتبر: %s" % exc
        if self.format == "svg" and "<svg" not in content.lower():
            return "SVG معتبر یافت نشد"
        return None


@dataclass
class SpecialistRole:
    id: str
    title: str
    purpose: str
    inputs: tuple[str, ...]
    responsibilities: tuple[str, ...]
    allowed_scopes: tuple[str, ...]
    artifacts: tuple[ArtifactSpec, ...]
    acceptance: tuple[str, ...]
    depends_on: tuple[str, ...] = ()
    product_types: tuple[str, ...] = ("*",)

    def can_use(self, scope: str) -> bool:
        return scope in self.allowed_scopes

    def instruction(self, goal: str, context: dict | None = None) -> str:
        ctx = context or {}
        lines = [
            "نقش تو: " + self.title,
            "وظیفه: " + self.purpose,
            "هدف کاربر: " + str(goal)[:1200],
            "ورودیهای موجود: " + ", ".join(self.inputs),
            "مسئولیتها: " + " | ".join(self.responsibilities),
            "معیار پذیرش: " + " | ".join(self.acceptance),
            "فقط ابزارهای مجاز سطح دسترسی " + ", ".join(self.allowed_scopes) + " را بکار بگیر.",
            "خروجی را دقیقاً بهصورت markdown کامل بنویس؛فقط خروجی بنویس، توضیح اضافی نده.",
        ]
        if ctx:
            lines.append("اطلاعات قبلی (قابل ردیابی): " +
                         str(ctx))
        for a in self.artifacts:
            secs = ("؛ بخشهای اجباری: " + ", ".join(a.required_sections)
                    if a.required_sections else "")
            lines.append("===FILE %s (%s)%s" % (a.path, a.format, secs))
        return "\n".join(lines)


ROLES: tuple[SpecialistRole, ...] = (
    SpecialistRole(
        "product-architect", "معمار محصول", "تحلیل نیاز، مخاطب و تعریف محصول",
        ("goal", "constraints"),
        ("Product Brief", "PRD", "non-goals", "success criteria"),
        ("project:read",),
        (ArtifactSpec("spec/product-brief.md", "md",
                      ("## Target User", "## Scope", "## Success Criteria")),),
        ("Product Briefrealبا مخاطب و معیار قابل تست",),
    ),
    SpecialistRole(
        "software-architect", "معمار نرمافزار", "معماری، فناوری و ساختار پروژه",
        ("product-brief.md", "project model"),
        ("ADR", "module boundaries", "test strategy"),
        ("project:read",),
        (ArtifactSpec("spec/architecture.md", "md",
                      ("## Decisions", "## Components", "## Test Strategy")),),
        ("تصمیمها قابل ردیابی و با ابزارهای موجود سازگار",),
        ("product-architect",),
    ),
    SpecialistRole(
        "ui-ux-designer", "طراح تجربه کاربری", "رابط فارسی RTL و Design System",
        ("product-brief.md", "architecture.md"),
        ("wireframe", "design tokens", "accessibility"),
        ("project:read", "project:write"),
        (ArtifactSpec("spec/design-spec.md", "md",
                      ("## Layout", "## Tokens", "## States")),
         ArtifactSpec("spec/wireframe.html", "html", ("<html", "dir=\"rtl\""))),
        ("رابطrealrender شود و حالتهای خالی/خطا/موفقیت تعریف شوند",),
        ("product-architect",),
    ),
    SpecialistRole(
        "game-designer", "طراح بازی", "مکانیک، مراحل و تعادل",
        ("product-brief.md",),
        ("mechanics", "levels", "balance"),
        ("project:read",),
        (ArtifactSpec("spec/game-design.md", "md",
                      ("## Mechanics", "## Levels", "## Balance")),),
        ("هر مرحله با معیارreal قابل تست",),
        ("product-architect",),
        ("webapp", "fullstack", "android", "webpage"),
    ),
    SpecialistRole(
        "visual-artist", "هنرمند بصری", "سبک هنری و داراییهایreal",
        ("product-brief.md", "game-design.md"),
        ("art direction", "asset manifest", "no fake assets"),
        ("project:read", "project:write"),
        (ArtifactSpec("spec/art-direction.md", "md",
                      ("## Style", "## Assets")),
         ArtifactSpec("spec/asset-manifest.json", "json")),
        ("فقط داراییهایreal موجود فهرست شوند؛ موارد ساختگی NOT VERIFIED",),
        ("product-architect",),
    ),
    SpecialistRole(
        "audio-designer", "طراح صدا", "مشخصات صوتی و هماهنگی",
        ("product-brief.md", "game-design.md"),
        ("audio spec", "real files only"),
        ("project:read",),
        (ArtifactSpec("spec/audio-spec.md", "md",
                      ("## Music", "## Effects", "## Availability")),),
        ("بدون ابزارreal، فقط مشخصات؛ ادعای فایل صوتی ممنوع است",),
        ("product-architect",),
    ),
    SpecialistRole(
        "developer", "توسعهدهنده", "پیادهسازیreal کد و داراییها",
        ("architecture.md", "design-spec.md"),
        ("source code", "tests", "build-ready layout"),
        ("project:read", "project:write", "shell:execute", "tests:run"),
        (ArtifactSpec("src/game.py", "py", ("def ",)),),
        ("کدreal قابل اجرا و تست",),
        ("software-architect", "ui-ux-designer"),
    ),
    SpecialistRole(
        "qa-engineer", "مهندس کیفیت", "تست مستقل عملکرد، رابط و امنیت",
        ("implementation", "product-brief.md"),
        ("independent tool execution", "verdicts", "evidence"),
        ("project:read", "project:write", "tests:run", "security:read",
         "shell:execute"),
        (ArtifactSpec("spec/test-report.md", "md",
                      ("## Results", "## Evidence")),),
        ("PASS فقط با شاهد ابزار واقعی؛NOT VERIFIED جدا ثبت شود",),
        ("developer",),
    ),
    SpecialistRole(
        "security-reviewer", "بازبین امنیت", "ممیزی مجوزها و محتوای قابل انتشار",
        ("test-report.md", "source"),
        ("secret scan", "permission audit", "release decision"),
        ("project:read", "security:read"),
        (ArtifactSpec("spec/security-report.md", "md",
                      ("## Findings", "## Decision")),),
        ("هیچ Secret واقعی در خروجی باقی نماند",),
        ("developer",),
    ),
)

# AF NEXT — Human-Gated Workflow roles. Additive: the original nine roles and
# their tests/planning stay untouched; these roles drive the staged studio DAG.
EXTRA_ROLES: tuple[SpecialistRole, ...] = (
    SpecialistRole("product-director", "مدیر محصول", "سند محصول، مخاطب و تجربهٔ موردنظر",
        ("goal", "constraints"), ("Product Brief", "audience", "experience goals"),
        ("project:read",), (ArtifactSpec("spec/product-brief.md", "md",
            ("## Target User", "## Scope", "## Success Criteria")),),
        ("سندrealبا مخاطب و معیار قابل بررسی",)),
    SpecialistRole("game-designer-gdd", "طراح بازی", "مکانیک، مراحل و جریان بازی",
        ("product-brief.md",), ("mechanics", "levels", "flow"),
        ("project:read",), (ArtifactSpec("spec/game-design.md", "md",
            ("## Mechanics", "## Levels", "## Flow")),),
        ("هر مرحله قابل تستreal",), ("product-director",)),
    SpecialistRole("art-director", "مدیر هنری", "سبک، رنگ، نور و فرم 2.5D",
        ("product-brief.md", "game-design.md"), ("style", "palette", "lighting"),
        ("project:read",), (ArtifactSpec("spec/art-direction.md", "md",
            ("## Style", "## Palette", "## Lighting")),),
        ("مرجع بصریrealو قابل پیادهسازی",), ("product-director",)),
    SpecialistRole("character-artist", "هنرمند شخصیت", "Character Bible و شیتreal",
        ("art-direction.md",), ("character bible", "sheet", "silhouette"),
        ("project:read", "project:write"),
        (ArtifactSpec("spec/character-bible.md", "md", ("## Identity", "## Silhouette")),
         ArtifactSpec("spec/character-sheet.svg", "svg")),
        ("SVGrealقابل نمایش و بزرگنمایی",), ("art-director",)),
    SpecialistRole("environment-artist", "هنرمند محیط", "طراحی محیط و نقشه صحنهها",
        ("art-direction.md", "game-design.md"), ("level map", "depth layers"),
        ("project:read", "project:write"),
        (ArtifactSpec("spec/environment-design.md", "md", ("## Layout", "## Depth")),
         ArtifactSpec("spec/level-map.svg", "svg")),
        ("نقشهrealقابل نمایش",), ("art-director",)),
    SpecialistRole("ui-game", "طراح UI/UX", "رابط، جریان تعامل و نمونه صفحات",
        ("product-brief.md", "art-direction.md"), ("flow", "wireframe"),
        ("project:read", "project:write"),
        (ArtifactSpec("spec/design-spec.md", "md", ("## Layout", "## States")),
         ArtifactSpec("spec/wireframe.html", "html", ("<html",))),
        ("صفحهرئال render شود",), ("product-director",)),
    SpecialistRole("technical-artist-25d", "Technical Artist 2.5D", "لایهبندی، عمق، دوربین و Pipeline",
        ("art-direction.md", "environment-design.md"), ("parallax", "camera", "asset pipeline"),
        ("project:read",), (ArtifactSpec("spec/technical-art-25d.md", "md",
            ("## Layers", "## Camera", "## Pipeline")),),
        ("معماری قابل ارتقا به 3D مستند باشد",), ("environment-artist", "ui-game")),
    SpecialistRole("animator", "انیماتور", "انیمیشن و حالتهای شخصیتها",
        ("character-bible.md", "technical-art-25d.md"), ("states", "timing"),
        ("project:read",), (ArtifactSpec("spec/animation-spec.md", "md",
            ("## States", "## Timing")),),
        ("حالتهاrealتعریف و قابل تست",), ("character-artist", "technical-artist-25d")),
    SpecialistRole("audio-game", "طراح صدا", "صدا، موسیقی و افکت واقعی",
        ("game-design.md",), ("sfx", "music", "availability"),
        ("project:read", "media:write"),
        (ArtifactSpec("spec/audio-spec.md", "md", ("## Music", "## Effects")),),
        ("فایل صوتیrealدر صورت وجود ابزار؛ Otherwise NOT VERIFIED",), ("game-designer-gdd",)),
    SpecialistRole("game-developer", "توسعهدهنده بازی", "پیادهسازی و یکپارچهای real",
        ("game-design.md", "technical-art-25d.md"), ("source", "integration"),
        ("project:read", "project:write", "tests:run"),
        (ArtifactSpec("src/game.py", "py", ("def ",)),),
        ("کدrealقابل اجرا",), ("animator", "audio-game", "ui-game")),
    SpecialistRole("qa-game", "مهندس کیفیت", "تست مستقل و Reportreal",
        ("src/game.py",), ("independent tools", "evidence"),
        ("project:read", "project:write", "tests:run", "security:read"),
        (ArtifactSpec("spec/test-report.md", "md", ("## Results", "## Evidence")),),
        ("PASS فقط با شاهد ابزار",), ("game-developer",)),
    SpecialistRole("security-game", "بازبین امنیت", "امنیت و حریم خصوصی",
        ("test-report.md",), ("secret scan", "permissions"),
        ("project:read", "security:read"),
        (ArtifactSpec("spec/security-report.md", "md", ("## Findings", "## Decision")),),
        ("گزارشrealبدون Secret",), ("game-developer",)),
    SpecialistRole("release-engineer", "مهندس انتشار", "ساخت، بستهبندی و شواهدrelease",
        ("test-report.md", "security-report.md"), ("build", "evidence"),
        ("project:read", "release:export"),
        (ArtifactSpec("spec/release-evidence.md", "md", ("## Artifact", "## Decision")),),
        ("خروجیrealبا هش و وضعیت تست",), ("qa-game", "security-game")),
    SpecialistRole("android-developer", "توسعهدهنده اندروید", "چیدمان minimal و ساخت APK",
        ("architecture.md",), ("manifest", "java", "build"),
        ("project:read", "project:write", "build:android"),
        (ArtifactSpec("spec/android-build-notes.md", "md", ("## Build", "## Limits")),),
        ("APKrealامضا شده",), ("developer",)),
    SpecialistRole("ai-engineer", "مهندس AI", "ماژول عامل و پیکربندی real",
        ("architecture.md",), ("module", "config", "provider"),
        ("project:read", "project:write"),
        (ArtifactSpec("spec/ai-module-spec.md", "md", ("## Module", "## Provider")),),
        ("بدون کلید جاسازیشده",), ("software-architect",)),
)

_ALL = {r.id: r for r in ROLES}
_ALL.update({r.id: r for r in EXTRA_ROLES})
_BY_ID = {r.id: r for r in ROLES}   # original DAG stays exactly as before




def get(role_id: str) -> SpecialistRole | None:
    return _ALL.get(role_id)


def for_product(product_type: str = "") -> list[SpecialistRole]:
    return [r for r in ROLES
            if "*" in r.product_types or product_type in r.product_types]


def parallel_groups(product_type: str = "") -> list[list[str]]:
    """Only independent roles may run together (bounded to 2 workers)."""
    roles = for_product(product_type)
    by_depth: dict[int, list[str]] = {}
    memo: dict[str, int] = {}

    def depth(r: SpecialistRole) -> int:
        if r.id in memo:
            return memo[r.id]
        memo[r.id] = 0 if not r.depends_on else 1 + max(
            depth(_BY_ID[d]) for d in r.depends_on if d in _BY_ID)
        return memo[r.id]

    for r in roles:
        by_depth.setdefault(depth(r), []).append(r.id)
    return [by_depth[k] for k in sorted(by_depth)]


# AF NEXT Phase 05 — domain specialist CAPABILITIES / SUBROLES.
# These are additive contracts in the single canonical catalog. They are
# activated only by the Phase 05 activation matrix; never all at once.
DOMAIN_ROLES: tuple[SpecialistRole, ...] = (
    SpecialistRole("backend-specialist", "Backend Specialist",
        "Server-side architecture and data flow (design only in Phase 05)",
        ("product-brief.md", "architecture.md"),
        ("service boundaries", "persistence design", "backend tests"),
        ("project:read", "project:write"),
        (ArtifactSpec("spec/backend-design.md", "md",
                      ("## Services", "## Data Flow", "## Tests")),),
        ("Service boundaries and data flow are traceable to the brief",),
        ("software-architect",), ("webapp", "api", "fullstack", "pwa")),
    SpecialistRole("frontend-specialist", "Frontend Specialist",
        "Client-side structure and interaction design",
        ("product-brief.md", "design-spec.md"),
        ("component structure", "state model", "client tests"),
        ("project:read", "project:write"),
        (ArtifactSpec("spec/frontend-design.md", "md",
                      ("## Components", "## State", "## Tests")),),
        ("Component and state design preserve the RTL interface contract",),
        ("ui-ux-designer",), ("webapp", "webpage", "pwa", "fullstack")),
    SpecialistRole("database-specialist", "Database Specialist",
        "Schema, indexing and data integrity design",
        ("architecture.md", "backend-design.md"),
        ("schema", "migration safety", "query constraints"),
        ("project:read", "project:write"),
        (ArtifactSpec("spec/database-design.md", "md",
                      ("## Schema", "## Migrations", "## Integrity")),),
        ("No destructive migration is proposed without explicit approval",),
        ("software-architect",), ("webapp", "api", "fullstack")),
    SpecialistRole("api-specialist", "API Specialist",
        "Contract-first API surface design",
        ("architecture.md", "backend-design.md"),
        ("endpoints", "error model", "compatibility"),
        ("project:read", "project:write"),
        (ArtifactSpec("spec/api-contract.md", "md",
                      ("## Endpoints", "## Errors", "## Compatibility")),),
        ("Every endpoint has an explicit input/output and error contract",),
        ("software-architect",), ("webapp", "api", "fullstack", "pwa")),
    SpecialistRole("devops-specialist", "DevOps Specialist",
        "Build/test pipeline design (no release execution in Phase 05)",
        ("architecture.md", "test-report.md"),
        ("pipeline stages", "environment limits", "verification"),
        ("project:read",),
        (ArtifactSpec("spec/devops-plan.md", "md",
                      ("## Pipeline", "## Limits", "## Verification")),),
        ("No deploy, publish or paid operation is proposed or executed",),
        ("developer",), ("webapp", "api", "fullstack", "android", "pwa")),
    SpecialistRole("mobile-ux-specialist", "Mobile UX Specialist",
        "Touch, viewport and offline behaviour for mobile delivery",
        ("product-brief.md", "design-spec.md"),
        ("touch targets", "viewport rules", "offline states"),
        ("project:read", "project:write"),
        (ArtifactSpec("spec/mobile-ux.md", "md",
                      ("## Touch", "## Viewport", "## Offline")),),
        ("Narrow viewport workflow is described without horizontal scrolling",),
        ("ui-ux-designer",), ("android", "pwa", "webapp")),
    SpecialistRole("application-architect", "Application Architecture Specialist",
        "Cross-platform application structure and module boundaries",
        ("product-brief.md",),
        ("module boundaries", "platform seams", "test strategy"),
        ("project:read",),
        (ArtifactSpec("spec/app-architecture.md", "md",
                      ("## Modules", "## Platform Seams", "## Tests")),),
        ("Domain and delivery platform remain independent decisions",),
        ("software-architect",), ("android", "webapp", "pwa", "fullstack")),
    SpecialistRole("level-designer", "Level Designer",
        "Level layout, progression and pacing",
        ("game-design.md",),
        ("level layout", "progression", "pacing"),
        ("project:read",),
        (ArtifactSpec("spec/level-design.md", "md",
                      ("## Layout", "## Progression", "## Pacing")),),
        ("Each level has a verifiable objective and exit condition",),
        ("game-designer",), ("game",)),
    SpecialistRole("game-balance-specialist", "Game Balance Specialist",
        "Difficulty curves and resource balance",
        ("game-design.md", "level-design.md"),
        ("difficulty curve", "resource economy", "risk limits"),
        ("project:read",),
        (ArtifactSpec("spec/balance-spec.md", "md",
                      ("## Difficulty", "## Economy", "## Limits")),),
        ("No numeric target is invented without a real source",),
        ("game-designer",), ("game",)),
    SpecialistRole("narrative-designer", "Narrative Designer",
        "Story structure, voice and content boundaries",
        ("product-brief.md",),
        ("narrative structure", "tone", "content limits"),
        ("project:read",),
        (ArtifactSpec("spec/narrative-design.md", "md",
                      ("## Structure", "## Tone", "## Limits")),),
        ("Narrative does not invent unsupported product claims",),
        (), ("game", "webapp", "android")),
    SpecialistRole("education-specialist", "Education Specialist",
        "Learning goals and audience definition",
        ("product-brief.md",),
        ("learning goals", "audience", "assessment map"),
        ("project:read",),
        (ArtifactSpec("spec/learning-goals.md", "md",
                      ("## Goals", "## Audience", "## Assessment")),),
        ("Learning goals are testable without fabricated metrics",),
        ("product-architect",), ("education",)),
    SpecialistRole("curriculum-designer", "Curriculum Designer",
        "Sequence, prerequisites and pacing of learning material",
        ("learning-goals.md",),
        ("module sequence", "prerequisites", "review points"),
        ("project:read",),
        (ArtifactSpec("spec/curriculum.md", "md",
                      ("## Sequence", "## Prerequisites", "## Reviews")),),
        ("Prerequisite graph has no unresolved cycle",),
        ("education-specialist",), ("education",)),
    SpecialistRole("learning-experience-specialist", "Learning Experience Specialist",
        "Practice loops, feedback and retention design",
        ("learning-goals.md", "curriculum.md"),
        ("practice loop", "feedback", "retention checks"),
        ("project:read",),
        (ArtifactSpec("spec/learning-experience.md", "md",
                      ("## Practice", "## Feedback", "## Retention")),),
        ("Feedback rules are observable and privacy-preserving",),
        ("education-specialist",), ("education",)),
    SpecialistRole("accessibility-specialist", "Accessibility Specialist",
        "Accessibility and inclusive interaction review",
        ("design-spec.md",),
        ("keyboard and touch access", "contrast", "screen reader labels"),
        ("project:read",),
        (ArtifactSpec("spec/accessibility.md", "md",
                      ("## Access", "## Contrast", "## Labels")),),
        ("Accessibility checks are explicit and testable",),
        ("ui-ux-designer",), ("webapp", "webpage", "android", "pwa",
                              "education", "game")),
    SpecialistRole("visual-designer", "Visual Designer",
        "Visual language and design tokens",
        ("product-brief.md", "design-spec.md"),
        ("tokens", "layout rhythm", "state visuals"),
        ("project:read", "project:write"),
        (ArtifactSpec("spec/visual-design.md", "md",
                      ("## Tokens", "## Layout", "## States")),),
        ("No professional image quality is claimed without real inspection",),
        ("product-architect",), ("webapp", "webpage", "android", "game")),
    SpecialistRole("music-sound-specialist", "Music/Sound Specialist",
        "Sound direction and availability notes (no generated audio in Phase 05)",
        ("product-brief.md",),
        ("sound palette", "licensing notes", "availability"),
        ("project:read",),
        (ArtifactSpec("spec/music-plan.md", "md",
                      ("## Palette", "## Licensing", "## Availability")),),
        ("No audio file or provider capability is claimed without proof",),
        ("audio-designer",), ("game", "education")),
)
