"""Fullstack scaffolder — builds a REAL runnable fullstack-python project.

Copies the vetted template (core/fullstack_template/) into
projects/<name>/ with placeholders substituted, then the standard factory
checker/runner/exporter take over unchanged. No model required for the
scaffold; the model can additionally customize the project via the normal
generate flow.
"""

import logging
import shutil
from pathlib import Path

from core import paths

log = logging.getLogger("ai-factory.scaffold")

TEMPLATE_DIR = Path(__file__).resolve().parent / "fullstack_template"
AI_TEMPLATE_DIR = Path(__file__).resolve().parent / "aimodule_template"
PWA_TEMPLATE_DIR = Path(__file__).resolve().parent / "pwa_template"
NODE_TEMPLATE_DIR = Path(__file__).resolve().parent / "nodeserver_template"


def scaffold(project: str, app_title: str | None = None) -> dict:
    """Create the standard fullstack project. Returns created file list."""
    d = paths.project_dir(project)
    title = (app_title or project).strip()[:60] or project
    app_id = "".join(c if c.isalnum() else "-" for c in project.lower())[:40]
    out = []
    for src in sorted(TEMPLATE_DIR.rglob("*")):
        if not src.is_file():
            continue
        if "__pycache__" in src.parts or src.suffix in (".pyc", ".pyo") \
                or src.name.endswith((".db", ".db-wal", ".db-shm", ".log")):
            continue          # runtime/cache files are never scaffolded
        rel = src.relative_to(TEMPLATE_DIR)
        target = d / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text(encoding="utf-8")
        text = (text.replace("{{APP_TITLE}}", title)
                    .replace("{{APP_ID}}", app_id))
        target.write_text(text, encoding="utf-8")
        out.append(rel.as_posix())
    (d / "data").mkdir(exist_ok=True)           # db dir (empty, no secrets)
    log.info("fullstack scaffold: %s (%d files)", project, len(out))
    return {"files": out, "app_title": title}


def scaffold_pwa(project: str, app_title: str | None = None) -> dict:
    """P8-0 — scaffold a standard installable PWA base.

    Copies core/pwa_template/ into projects/<name>/ with placeholders
    substituted. Binary assets (icons/*.png) are copied verbatim — never
    text-substituted. The result passes the base checker (valid HTML/JS)
    and is served by the standard static-web runner; the model customizes
    APP CONTENT on top without touching the PWA plumbing.
    """
    d = paths.project_dir(project)
    title = (app_title or project).strip()[:60] or project
    out = []
    for src in sorted(PWA_TEMPLATE_DIR.rglob("*")):
        if not src.is_file() or "__pycache__" in src.parts:
            continue
        rel = src.relative_to(PWA_TEMPLATE_DIR)
        target = d / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".png":                 # binary asset — verbatim
            target.write_bytes(src.read_bytes())
            out.append(rel.as_posix())
            continue
        text = src.read_text(encoding="utf-8")
        text = text.replace("{{APP_TITLE}}", title)
        target.write_text(text, encoding="utf-8")
        out.append(rel.as_posix())
    log.info("pwa scaffold: %s (%d files)", project, len(out))
    return {"files": out, "app_title": title}


def scaffold_node(project: str, app_title: str | None = None) -> dict:
    """AF-13 — scaffold a real runnable node-server project (stdlib only).

    Copies core/nodeserver_template/ into projects/<name>/ with placeholders
    substituted. server.js uses Node's built-in http only (no external
    deps); tests/ is a node:test suite doing real HTTP against the real
    server. The standard factory runner detects it via package.json(main)
    and runs it with PORT env; run_tests executes `node --test`.
    """
    d = paths.project_dir(project)
    title = (app_title or project).strip()[:60] or project
    app_id = "".join(c if c.isalnum() else "-" for c in project.lower())[:40]
    out = []
    for src in sorted(NODE_TEMPLATE_DIR.rglob("*")):
        if not src.is_file() or "__pycache__" in src.parts:
            continue
        rel = src.relative_to(NODE_TEMPLATE_DIR)
        target = d / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text(encoding="utf-8")
        text = (text.replace("{{APP_TITLE}}", title)
                    .replace("{{APP_ID}}", app_id))
        target.write_text(text, encoding="utf-8")
        out.append(rel.as_posix())
    log.info("node-server scaffold: %s (%d files)", project, len(out))
    return {"files": out, "app_title": title}


def scaffold_ai_module(project: str, dir_path: Path | None = None) -> dict:
    """Install the OPTIONAL standalone AI connector into a project.

    Only AI-capable projects call this — nothing is forced on plain
    webpage/fullstack projects. The module is stdlib-only and OpenCode-free
    (core/aimodule_template/app/aiclient.py is the single canonical source;
    test_aiclient.py proves it with a local mock service).

    dir_path: for factory-internal tests only (temp dirs); production always
    passes None and the project directory is resolved from the name.
    """
    d = paths.project_dir(project) if dir_path is None else Path(dir_path)
    out = []
    for src in sorted(AI_TEMPLATE_DIR.rglob("*")):
        if not src.is_file() or "__pycache__" in src.parts:
            continue
        rel = src.relative_to(AI_TEMPLATE_DIR)
        target = d / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel.name in ("config.json", "ai.json") and target.exists():
            out.append(rel.as_posix() + " (kept existing)")
            continue          # never clobber the project's own settings
        text = src.read_text(encoding="utf-8")
        text = text.replace("{{APP_TITLE}}", project)
        target.write_text(text, encoding="utf-8")
        out.append(rel.as_posix())
    log.info("ai module scaffold: %s (%d files)", project, len(out))
    return {"files": out}
