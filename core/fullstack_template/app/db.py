"""SQLite layer — connection, schema, session helpers.

Real persistence: the database file lives in data/app.db next to server.py,
so customers/users survive stop & restart (verified by the factory tests).
All queries are parameterized — never string-formatted SQL.
"""

import os
import secrets
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def db_file() -> str:
    """Computed lazily (not at import) so test isolation via AF_FORCE_DB /
    AF_DB_DIR set by server.py main() / tests always wins."""
    return os.environ.get("AF_FORCE_DB") or os.path.join(
        ROOT, "data", "app.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    pw_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created INTEGER NOT NULL,
    expires INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_customers_owner ON customers(owner_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""


def connect() -> sqlite3.Connection:
    f = db_file()
    os.makedirs(os.path.dirname(f), exist_ok=True)
    conn = sqlite3.connect(f)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)


def new_token() -> str:
    return secrets.token_hex(32)


def now() -> int:
    return int(time.time())
