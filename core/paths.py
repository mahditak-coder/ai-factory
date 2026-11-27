"""Shared filesystem path validation for AI Factory (host + OpenCode bridge)."""

import re
from pathlib import Path

import core.config as config

_Name = re.compile(config.PROJECT_NAME_RE)


def project_name_valid(project: str) -> bool:
    return bool(_Name.match(project or ""))


def resolve_safe(base: Path, rel: str) -> Path:
    """Resolve *rel* under *base* and guarantee it cannot escape the base dir."""
    rel = (rel or "").strip()
    if rel.startswith("/") or rel.startswith("\\"):
        raise PermissionError("absolute paths are not allowed")
    candidate = (base / rel).resolve()
    base_resolved = base.resolve()
    if candidate != base_resolved and base_resolved not in candidate.parents:
        raise PermissionError("path escapes the project directory")
    return candidate


def project_dir(project: str) -> Path:
    """Validate a project name and return its directory (must exist)."""
    if not project_name_valid(project):
        raise PermissionError("invalid project name")
    path = resolve_safe(config.PROJECTS_DIR, project)
    if not path.is_dir():
        raise FileNotFoundError("project does not exist: " + project)
    return path


def project_path(project: str, rel: str) -> Path:
    """Validate a project name and resolve a file path inside it."""
    return resolve_safe(project_dir(project), rel)
