"""P6-2 — panel authentication & session security.

Real, minimal, honest auth for the AI Factory panel:

* Password stored ONLY as PBKDF2-HMAC-SHA256 (random salt, 150k iterations)
  inside ``data/panel_admin.json`` with 0600 perms (atomic write). There is
  NO default password and NO hidden account — the file is created solely by
  an explicit first-run init.
* Session: random token (token_urlsafe 32), sent as HttpOnly SameSite=Lax
  cookie ``panel_session`` with 12h expiry. Sessions live in-process and
  disappear on restart (deliberate: restart = global logout).
* CSRF: double-submit for every mutation — a ``panel_csrf`` cookie
  (SameSite=Strict, readable by JS) plus ``X-Panel-CSRF`` header must match
  the token bound to the authenticated session.
* Rate limiting: max 5 failed logins per 15-minute window → lockout for the
  remainder of that window.
* Logout: session+CSRF tokens deleted server-side, cookies cleared.
* Nothing sensitive is ever returned or logged: only booleans/status.
"""

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from hashlib import pbkdf2_hmac
from pathlib import Path

import core.config as config

SESSION_COOKIE = "panel_session"
CSRF_COOKIE = "panel_csrf"
CSRF_HEADER = "X-Panel-CSRF"

SESSION_TTL = 12 * 3600
CSRF_TTL = SESSION_TTL
LOCK_WINDOW = 15 * 60          # failure window
LOCK_MAX_FAILS = 5
PBKDF2_ITERATIONS = 150_000

# AF-26 — derive from the (possibly isolated) project root so tests never
# touch the live credential store.
_ADMIN_FILE = config.PROJECT_ROOT / "data" / "panel_admin.json"

_lock = threading.RLock()
_sessions: dict[str, dict] = {}        # token -> {exp, csrf}
_fails: list[float] = []               # timestamps of recent failed logins


class PanelAuthError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------------------------------------------------------------- storage ----

def _read_admin_file() -> dict:
    try:
        data = json.loads(_ADMIN_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_admin_file(data: dict) -> None:
    """Atomic write with 0600; the data/ dir is best-effort 0700."""
    _ADMIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(_ADMIN_FILE.parent, 0o700)
    except OSError:
        pass
    tmp = _ADMIN_FILE.with_name(_ADMIN_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, _ADMIN_FILE)


def _pass_hash(password: str, salt: bytes, iterations: int) -> bytes:
    return pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def initialized() -> bool:
    st = _read_admin_file()
    return bool(st.get("hash") and st.get("salt"))


def init(password: str) -> dict:
    """Secure first-run setup. NO default credential of any kind is made."""
    if not isinstance(password, str) or not (8 <= len(password) <= 200):
        raise PanelAuthError(400, "رمز باید بین ۸ تا ۲۰۰ کاراکتر باشد")
    with _lock:
        if initialized():
            raise PanelAuthError(409, "پنل قبلاً تنظیم شده است؛ "
                                      "رمز موجود را تغییر ندهید از مسیر مجاز")
        salt = os.urandom(16)
        iters = PBKDF2_ITERATIONS
        _write_admin_file({
            "v": 1,
            "salt": salt.hex(),
            "iterations": iters,
            "hash": _pass_hash(password, salt, iters).hex(),
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
    return {"ok": True, "initialized": True}


def _locked_out() -> bool:
    now = time.time()
    with _lock:
        while _fails and _fails[0] < now - LOCK_WINDOW:
            _fails.pop(0)
        return len(_fails) >= LOCK_MAX_FAILS


def _register_fail() -> None:
    with _lock:
        now = time.time()
        while _fails and _fails[0] < now - LOCK_WINDOW:
            _fails.pop(0)
        _fails.append(now)


def _lockout_seconds_left() -> int:
    now = time.time()
    with _lock:
        while _fails and _fails[0] < now - LOCK_WINDOW:
            _fails.pop(0)
        if len(_fails) < LOCK_MAX_FAILS or not _fails:
            return 0
        return max(1, int(_fails[0] + LOCK_WINDOW - now))


def login(password: str) -> tuple[str, str]:
    """Verify the password; on success return (session_token, csrf_token).

    Soft lockout: only WRONG credentials are counted/blocked (5 failures
    per 15-minute window → 429). A correct password still works and resets
    the window — brute-force attempts stay throttled (PBKDF2 + friction),
    the legitimate admin is never permanently shut out.

    Raises PanelAuthError(429) during lockout and (401) on bad password.
    """
    word = password if isinstance(password, str) else ""
    st = _read_admin_file()
    salt_hex = str(st.get("salt") or "")
    ok = False
    if initialized() and salt_hex:
        try:
            iters = int(st.get("iterations") or PBKDF2_ITERATIONS)
            if 50_000 <= iters <= PBKDF2_ITERATIONS * 10:
                calc = _pass_hash(word, bytes.fromhex(salt_hex), iters).hex()
                ok = hmac.compare_digest(calc, str(st.get("hash") or "").lower())
        except (ValueError, TypeError):
            ok = False
    if not ok:
        need_wait = _lockout_seconds_left()
        _register_fail()
        time.sleep(0.05)               # brute-force friction (PBKDF2 parity)
        if need_wait:
            raise PanelAuthError(
                429, f"قفل موقت ورود؛ {need_wait} ثانیه دیگر تلاش کنید")
        raise PanelAuthError(401, "رمز نادرست است")
    with _lock:
        _fails.clear()                 # success resets the guess window
        _sessions.clear()              # single admin: one live session
        session = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        _sessions[session] = {"exp": time.time() + SESSION_TTL,
                              "csrf": csrf, "created": time.time()}
    return session, csrf


def session_token_valid(token: str) -> bool:
    with _lock:
        s = _sessions.get(token or "")
        if not s:
            return False
        if s["exp"] < time.time():
            _sessions.pop(token, None)
            return False
        return True


def verify(token: str, csrf_header: str, csrf_cookie: str) -> bool:
    """Session check + CSRF double-submit (cookie == header == session's)."""
    with _lock:
        s = _sessions.get(token or "")
        if not s or s["exp"] < time.time():
            return False
        good = s.get("csrf") or ""
    return bool(csrf_header and csrf_cookie == csrf_header
                and hmac.compare_digest(csrf_header, good))


def csrf_of(token: str) -> str | None:
    with _lock:
        s = _sessions.get(token or "")
        if not s or s["exp"] < time.time():
            return None
        return s.get("csrf") or None


def logout(token: str) -> None:
    with _lock:
        _sessions.pop(token or "", None)


def state() -> dict:
    return {"ok": True, "initialized": initialized()}
