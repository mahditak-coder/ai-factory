"""AF NEXT Phase 1 — independent ModelGateway contract.

Core no longer reaches into the OpenCode bridge by scattered imports: it talks
to one gateway object.  The first REAL adapter is OpenCodeGateway, a thin
delegation layer over the existing, battle-tested ``core.opencode_bridge``.
No session, permission, reasoning, streaming or quota behavior is re-implemented.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from core.opencode_bridge import OcError  # re-export: callers use the contract


@dataclass
class SessionStateView:
    """Provider-neutral view of a model session's REAL state."""
    session: str
    outcome: str | None = None
    running: bool = False
    busy: int = 0
    idle: int = 0
    updated: int | None = None
    created: int | None = None
    model: dict | None = None
    last_error: dict | None = None
    pending_permissions: list[str] = field(default_factory=list)
    awaiting_permission: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "SessionStateView":
        return cls(session=str(d.get("session") or ""),
                   outcome=d.get("outcome"), running=bool(d.get("running")),
                   busy=int(d.get("busy") or 0), idle=int(d.get("idle") or 0),
                   updated=d.get("updated"), created=d.get("created"),
                   model=d.get("model"), last_error=d.get("last_error"),
                   pending_permissions=list(d.get("pending_permissions") or []),
                   awaiting_permission=bool(d.get("awaiting_permission")))


@runtime_checkable
class ModelGateway(Protocol):
    """The ONLY model-facing surface Core is allowed to use."""

    name: str

    def ensure_available(self) -> bool: ...
    def status(self) -> dict: ...
    def list_models(self) -> list[dict]: ...
    def sessions(self, project: str) -> list[dict]: ...
    def create_session(self, project: str, title: str | None = None) -> dict: ...
    def prompt(self, project: str, session_id: str, text: str,
               model: str | None = None, variant: str | None = None,
               agent: str | None = None) -> dict: ...
    def history(self, session_id: str) -> dict: ...
    def state(self, session_id: str) -> dict: ...
    def permissions(self, session_id: str) -> list[dict]: ...
    def reply_permission(self, session_id: str, request_id: str,
                         decision: str, message: str | None = None) -> None: ...
    def reply_once(self, session_id: str, request_id: str, decision: str,
                   message: str | None = None) -> bool: ...
    def expire_permission(self, session_id: str, request_id: str) -> bool: ...
    def headless_permissions(self, project: str, session_id: str,
                             task_id: str = "") -> dict: ...
    def interrupt(self, session_id: str) -> bool: ...
    def is_quota_error(self, text: str) -> bool: ...
    def record_tool_events(self, project: str, session_id: str,
                           task_id: str = "") -> int: ...
    def start_permission_watch(self, ttl_s: float | None = None) -> None: ...
    def session_location(self, session_id: str) -> str | None: ...
    def test_model(self, project: str, provider_id: str, model_id: str,
                   timeout_s: float = 60) -> dict: ...


class OpenCodeGateway:
    """Real adapter — delegates to the existing OpenCode bridge (v2.2.6)."""

    name = "opencode"

    def __init__(self):
        from core import opencode_bridge as bridge
        self._b = bridge

    def ensure_available(self) -> bool:
        return self._b.ensure_running()

    def status(self) -> dict:
        return self._b.status()

    def list_models(self) -> list[dict]:
        return self._b.models()

    def sessions(self, project: str) -> list[dict]:
        return self._b.sessions(project)

    def create_session(self, project: str, title: str | None = None) -> dict:
        return self._b.create_session(project, title)

    def prompt(self, project: str, session_id: str, text: str,
               model: str | None = None, variant: str | None = None,
               agent: str | None = None) -> dict:
        return self._b.prompt(project, session_id, text, model=model,
                              variant=variant, agent=agent)

    def history(self, session_id: str) -> dict:
        return self._b.history(session_id)

    def state(self, session_id: str) -> dict:
        return self._b.state(session_id)

    def permissions(self, session_id: str) -> list[dict]:
        return self._b.permissions(session_id)

    def reply_permission(self, session_id: str, request_id: str,
                         decision: str, message: str | None = None) -> None:
        return self._b.permission_reply(session_id, request_id, decision,
                                        message=message)

    def reply_once(self, session_id: str, request_id: str, decision: str,
                   message: str | None = None) -> bool:
        return self._b.reply_once(session_id, request_id, decision,
                                  message=message)

    def expire_permission(self, session_id: str, request_id: str) -> bool:
        return self._b.expire_permission(session_id, request_id)

    def headless_permissions(self, project: str, session_id: str,
                             task_id: str = "") -> dict:
        return self._b.reply_headless(project, session_id, task_id)

    def interrupt(self, session_id: str) -> bool:
        return self._b.interrupt(session_id)

    def is_quota_error(self, text: str) -> bool:
        return self._b.is_quota_error(text)

    def record_tool_events(self, project: str, session_id: str,
                           task_id: str = "") -> int:
        return self._b.record_tool_events(project, session_id, task_id)

    def start_permission_watch(self, ttl_s: float | None = None) -> None:
        return self._b.start_permission_watch(ttl_s)

    def session_location(self, session_id: str) -> str | None:
        return self._b._session_location(session_id)

    def test_model(self, project: str, provider_id: str, model_id: str,
                   timeout_s: float = 60) -> dict:
        return self._b.test_model(project, provider_id, model_id, timeout_s)


_gate_lock = threading.Lock()
_gateway: ModelGateway | None = None


def gateway() -> ModelGateway:
    """Current REAL gateway (OpenCode adapter by default)."""
    global _gateway
    with _gate_lock:
        if _gateway is None:
            _gateway = OpenCodeGateway()
        return _gateway


def validate_gateway(adapter: object) -> list[str]:
    """Return missing protocol methods (opt-in strict conformance check)."""
    required = tuple(name for name in dir(ModelGateway)
                     if not name.startswith("_"))
    return [name for name in required if not callable(getattr(adapter, name, None))]


def set_gateway(adapter: ModelGateway | None, *, strict: bool = False) -> None:
    """Replace the adapter (tests / future providers). None restores default.

    ``strict=True`` validates the full ModelGateway protocol without ever
    changing the permissive default used by existing tests.
    """
    global _gateway
    if adapter is not None and strict:
        missing = validate_gateway(adapter)
        if missing:
            raise TypeError("incomplete ModelGateway adapter: " + ",".join(missing))
    with _gate_lock:
        _gateway = adapter
