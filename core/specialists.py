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
        ("project:read", "tests:run", "security:read", "shell:execute"),
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
        ("project:read", "tests:run", "security:read"),
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
