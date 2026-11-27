"""Authentication — PBKDF2 password hashing, sessions, roles.

Security decisions (real, not documented-only):
  * passwords are never stored raw — PBKDF2-HMAC-SHA256 with per-user salt
  * session tokens are 32 random bytes, stored hashed in DB client side is
    a hex token; the cookie is HttpOnly
  * the FIRST registered user becomes the admin (local preview pattern —
    documented in README); afterwards everyone is a normal user
  * admin endpoints are checked server-side on every request
"""

import hashlib
import hmac
import os
import secrets

from .db import connect, new_token, now

SESSION_TTL = 24 * 3600
ITERATIONS = 100_000

_COOKIE_OPTS = "; Path=/; Max-Age=86400; HttpOnly; SameSite=Lax"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt, ITERATIONS)
    return "pbkdf2$%d$%s$%s" % (ITERATIONS, salt.hex(), digest.hex())


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, hash_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def register(conn, username: str, password: str) -> dict:
    """Register a user. First user of a fresh DB becomes admin."""
    row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
    role = "admin" if row["c"] == 0 else "user"
    pw = hash_password(password)
    cur = conn.execute(
        "INSERT INTO users (username, pw_hash, role, created) "
        "VALUES (?, ?, ?, ?)", (username, pw, role, now()))
    return {"id": cur.lastrowid, "role": role}


def authenticate(conn, username: str, password: str):
    row = conn.execute(
        "SELECT id, pw_hash, role FROM users WHERE username = ?",
        (username,)).fetchone()
    if row is None or not verify_password(password, row["pw_hash"]):
        return None
    return {"id": row["id"], "role": row["role"]}


def start_session(conn, user: dict):
    token = new_token()
    t = now()
    conn.execute(
        "INSERT INTO sessions (token, user_id, created, expires) "
        "VALUES (?, ?, ?, ?)",
        (hashlib.sha256(token.encode()).hexdigest(), user["id"],
         t, t + SESSION_TTL))
    return token


def current_user(conn, cookies: dict):
    raw = cookies.get("af_session")
    if not raw:
        return None
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    row = conn.execute(
        "SELECT u.id, u.username, u.role FROM sessions s "
        "JOIN users u ON u.id = s.user_id "
        "WHERE s.token = ? AND s.expires > ?",
        (token_hash, now())).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "username": row["username"], "role": row["role"]}


def end_session(conn, cookies: dict) -> bool:
    raw = cookies.get("af_session")
    if not raw:
        return False
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    cur = conn.execute("DELETE FROM sessions WHERE token = ?", (token_hash,))
    return cur.rowcount > 0


def clear_cookie() -> str:
    return "af_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"


def session_cookie(token: str) -> str:
    return "af_session=%s%s" % (token, _COOKIE_OPTS)


def purge_expired(conn) -> None:
    conn.execute("DELETE FROM sessions WHERE expires <= ?", (now(),))
