"""AF-25 — one-shot, scoped confirmations for sensitive Smart Release steps.

A proposal (create a repo, choose its name/visibility, resolve a conflict…)
is stored as a short-lived, single-use token. The user confirms **in the same
conversation** by sending the token back with an explicit decision; the token
is bound to the project (and optionally the chat session) so it cannot be
reused for another project.

The token is a random handle, NOT a credential: no GitHub token or password
is ever stored here. Store is 0600, atomic, append-evicted.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

import core.config as config

_STORE = config.PROJECT_ROOT / "data" / "smart_release_confirmations.json"
_TTL = 15 * 60          # 15 minutes
_MAX = 50


class ConfirmError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def _load() -> dict:
    try:
        d = json.loads(_STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("items"), dict) else {"items": {}}
    except (OSError, ValueError):
        return {"items": {}}


def _save(d: dict) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(_STORE)


def create(project: str, action: str, payload: dict,
           question_fa: str, session_id: str = "") -> dict:
    """Register a pending decision. Returns the handle to show the user."""
    token = secrets.token_urlsafe(18)
    d = _load()
    items = d["items"]
    now = time.time()
    for k in [k for k, v in items.items() if (v.get("expires") or 0) < now]:
        items.pop(k, None)
    items[token] = {
        "project": str(project), "action": str(action),
        "payload": payload, "question_fa": str(question_fa),
        "session_id": str(session_id or ""),
        "created": now, "expires": now + _TTL,
    }
    while len(items) > _MAX:
        items.pop(min(items, key=lambda k: items[k].get("created") or 0))
    _save(d)
    return {"confirm_token": token, "question": question_fa,
            "expires_in_s": _TTL, "project": project, "action": action}


def peek(token: str, project: str = "", session_id: str = "") -> dict:
    """Read a still-valid proposal without consuming it."""
    v = _load()["items"].get(str(token or ""))
    if not v:
        raise ConfirmError("پیام تأیید یافت نشد یا منقضی شد", 404)
    if (v.get("expires") or 0) < time.time():
        raise ConfirmError("زمان تأیید منقضی شد — دوباره درخواست را بدهید", 410)
    if project and v.get("project") != project:
        raise ConfirmError("این تأیید به پروژهٔ دیگری تعلق دارد", 403)
    if session_id and v.get("session_id") and v["session_id"] != session_id:
        raise ConfirmError("این تأیید به نشست دیگری تعلق دارد", 403)
    return dict(v)


def consume(token: str, project: str = "", session_id: str = "") -> dict:
    """Validate AND remove a proposal (single use)."""
    v = peek(token, project, session_id)
    d = _load()
    d["items"].pop(str(token), None)
    _save(d)
    return v


def cancel(token: str, project: str = "", session_id: str = "") -> dict:
    v = peek(token, project, session_id)
    d = _load()
    d["items"].pop(str(token), None)
    _save(d)
    return v
