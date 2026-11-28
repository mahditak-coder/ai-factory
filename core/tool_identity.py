"""Server-controlled tool identity and bounded infrastructure capabilities.

The only trusted issuance boundary is server memory: an issuer key generated
at import time, or a validated authenticated panel session. Untrusted request
paths (model prompts, caller JSON, scope strings) cannot mint capabilities.
Capabilities are bound to exact project, tool, scope and expiry.
"""
from __future__ import annotations

import secrets
import threading
import time

from core import panelauth

_ISSUER_KEY = secrets.token_urlsafe(32)
_CAPS: dict[str, dict] = {}
_LOCK = threading.RLock()
_DEFAULT_TTL_S = 300


class IdentityError(RuntimeError):
    pass


def server_issuer_key() -> str:
    """Server-internal key for trusted infrastructure modules only.

    Never read from requests, prompts, model output or caller-supplied data.
    """
    return _ISSUER_KEY


def issue_infrastructure_capability(project: str, tool_id: str, scope: str,
                                    *, issuer_key: str = "",
                                    session_id: str = "",
                                    ttl_s: int = _DEFAULT_TTL_S) -> str:
    """Mint a bounded capability from a TRUSTED server-side path.

    Trust is established by the in-process issuer key or by a validated
    authenticated panel session. Caller-supplied principal strings are ignored.
    """
    if not project or not tool_id or not scope:
        raise IdentityError("capability-bindings-required")
    trusted = False
    if issuer_key and secrets.compare_digest(str(issuer_key), _ISSUER_KEY):
        trusted = True
    if not trusted and session_id and panelauth.principal_for_session(session_id):
        trusted = True
    if not trusted:
        raise IdentityError("untrusted-capability-issuance")
    token = secrets.token_urlsafe(24)
    with _LOCK:
        _CAPS[token] = {"project": str(project), "tool_id": str(tool_id),
                        "scope": str(scope),
                        "expires": time.time() + max(1, int(ttl_s)),
                        "principal": ("panel_admin" if session_id else
                                      "server:infrastructure")}
    return token


def validate_capability(token: str, project: str, tool_id: str,
                        scope: str) -> bool:
    with _LOCK:
        cap = _CAPS.get(str(token or ""))
    if cap is None:
        return False
    if cap.get("expires", 0) < time.time():
        return False
    return (cap.get("project") == str(project) and
            cap.get("tool_id") == str(tool_id) and
            cap.get("scope") == str(scope))
