"""Server-controlled tool identity with NO retrievable issuer secret.

Authority is minted only from:
  * an allowlisted server module calling with a registered service identity; or
  * a genuinely validated authenticated panel session.

There is no exported key. The trust boundary is server-side code identity
(call stack) plus server-side session validation, never a caller string.
"""
from __future__ import annotations

import inspect
import secrets
import threading
import time

from core import panelauth

_CAPS: dict[str, dict] = {}
_LOCK = threading.RLock()
_DEFAULT_TTL_S = 300

# Canonical service identities with bounded scopes. Project/tool are bound per
# issued capability and revalidated at the tool boundary.
_SERVICES: dict[str, dict] = {
    "infrastructure": {"scopes": frozenset((
        "project:read", "project:write", "tests:run", "security:read",
        "shell:execute", "image:generate", "image:write", "media:write"))},
    "quality-gate": {"scopes": frozenset((
        "project:read", "project:write", "tests:run", "security:read",
        "shell:execute"))},
    "workflow-executor": {"scopes": frozenset((
        "project:read", "project:write", "tests:run", "shell:execute"))},
}
_TRUSTED_MODULES = frozenset((
    "core.quality_gate", "core.workflow_executor", "core.agent_collaboration"))


class IdentityError(RuntimeError):
    pass


def registered_services() -> dict[str, list[str]]:
    """Read-only view of service identities (no authority is exposed)."""
    return {k: sorted(v["scopes"]) for k, v in _SERVICES.items()}


def issue_service_capability(service_id: str, project: str, tool_id: str,
                             scope: str, *, session_id: str = "",
                             ttl_s: int = _DEFAULT_TTL_S) -> str:
    """Mint a bounded capability from a trusted server-side path only."""
    service = _SERVICES.get(str(service_id or ""))
    if service is None or scope not in service["scopes"]:
        raise IdentityError("service-scope-denied")
    if not project or not tool_id:
        raise IdentityError("capability-bindings-required")
    caller = ""
    frame = inspect.currentframe()
    try:
        caller = str((frame.f_back.f_globals if frame and frame.f_back else {})
                     .get("__name__") or "")
    finally:
        del frame
    trusted_module = caller in _TRUSTED_MODULES
    principal = ""
    if session_id:
        principal = str(panelauth.principal_for_session(session_id) or "")
    if not trusted_module and not principal:
        raise IdentityError("untrusted-capability-issuance")
    token = secrets.token_urlsafe(24)
    with _LOCK:
        _CAPS[token] = {"project": str(project), "tool_id": str(tool_id),
                        "scope": str(scope),
                        "expires": time.time() + max(1, int(ttl_s)),
                        "principal": principal or "service:" + service_id}
    return token


def validate_capability(token: str, project: str, tool_id: str,
                        scope: str) -> bool:
    with _LOCK:
        cap = _CAPS.get(str(token or ""))
    if cap is None or cap.get("expires", 0) < time.time():
        return False
    return (cap.get("project") == str(project) and
            cap.get("tool_id") == str(tool_id) and
            cap.get("scope") == str(scope))
