"""JSON API — routing, validation, CSRF, role checks, CRUD (customers).

Rules enforced here (server-side, real):
  * every mutating request (POST/DELETE) needs a matching CSRF token:
    cookie `af_csrf` == header `X-CSRF-Token` (double-submit cookie)
  * login/register need a csrf too — the browser obtains it via
    GET /api/csrf, which sets the cookie (readable by JS, NOT HttpOnly)
  * all SQL is parameterized
  * validation errors return 400 with a Persian message, never internals
  * admin endpoints return 403 for role=user — checked every request
"""

import re
import secrets

from . import auth
from .db import connect

_CSRF_COOKIE = "af_csrf=; Path=/"
CSRF_RE = re.compile(r"^[0-9a-f]{64}$")

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
_PHONE_RE = re.compile(r"^[0-9+\- ]{4,20}$")

_SESSION_COOKIE = "af_session"

_OK_CSRF = 403
_OK_AUTH = 401


def _csrf_ok(cookies: dict, headers: dict, data) -> tuple:
    lookup = {k.lower(): v for k, v in (headers or {}).items()}
    token = ((data or {}).get("csrf_token") if isinstance(data, dict)
             else "") or lookup.get("x-csrf-token") or ""
    cookie_token = cookies.get("af_csrf") or ""
    if not (CSRF_RE.match(token) and CSRF_RE.match(cookie_token)
            and secrets.compare_digest(token, cookie_token)):
        return (403, {"ok": False, "error": "CSRF — درخواست تایید نشد"}, None)
    return None


def _session_user(conn, cookies):
    return auth.current_user(conn, cookies)


def _public(u):
    if not u:
        return None
    return {"id": u["id"], "username": u["username"], "role": u["role"]}


def _require_admin(u):
    if not u:
        return (_OK_AUTH, {"ok": False, "error": "ابتدا وارد شوید"}, None)
    if u["role"] != "admin":
        return (403, {"ok": False, "error": "مجوز مدیر لازم است"}, None)
    return None


# ------------------------------------------------------------------ routes --

def dispatch(method: str, path: str, data, cookies: dict, headers):
    headers = {k: v for k, v in (headers or {}).items()} if headers else {}
    with connect() as conn:
        auth.purge_expired(conn)

        # ---- CSRF bootstrap (GET, sets readable cookie) ----
        if method == "GET" and path == "csrf":
            token = secrets.token_hex(32)
            return 200, {"ok": True, "csrf_token": token}, [
                "af_csrf=%s; Path=/; SameSite=Lax" % token]

        # ---- auth ----
        if method == "POST" and path == "auth/register":
            guard = _csrf_ok(cookies, headers, data)
            if guard: return guard[:2]
            valid = _validate_register(conn, data)
            if isinstance(valid, tuple):
                return valid[:2]
            user = auth.register(conn, valid["username"], valid["pw"])
            token = auth.start_session(conn, user)
            return 201, {"ok": True, "user": _public(
                auth.current_user(conn,
                                  {"af_session": token}))}, [
                auth.session_cookie(token)]

        if method == "POST" and path == "auth/login":
            guard = _csrf_ok(cookies, headers, data)
            if guard: return guard[:2]
            username = (data or {}).get("username") or ""
            password = (data or {}).get("password") or ""
            user = auth.authenticate(conn, username, password)
            if not user:
                return 401, {"ok": False, "error": "نام کاربری یا رمز نادرست است"}
            token = auth.start_session(conn, user)
            return 200, {"ok": True, "user": _public(
                auth.current_user(conn, {"af_session": token}))}, [
                auth.session_cookie(token)]

        if method == "POST" and path == "auth/logout":
            auth.end_session(conn, cookies)
            return 200, {"ok": True, "logged_out": True}, [auth.clear_cookie()]

        me = _session_user(conn, cookies)

        if method == "GET" and path == "me":
            if not me:
                return 401, {"ok": False, "error": "وارد نشده‌اید"}
            return 200, {"ok": True, "user": _public(me)}

        # ---- admin area (role check on EVERY request) ----
        if path.startswith("admin/"):
            guard = _require_admin(me)
            if guard: return guard[:2]
            return _admin_route(conn, method, path, me, data)

        # ---- CRUD: customers ----
        if path == "customers" or path.startswith("customers/"):
            if not me:
                return 401, {"ok": False, "error": "ابتدا وارد شوید"}
            if method == "GET":
                return _list_customers(conn, me)
            if method == "POST":
                guard = _csrf_ok(cookies, headers, data)
                if guard: return guard[:2]
                m = re.match(r"^customers/(\d+)/update$", path)
                if m:
                    return _update_customer(conn, me, int(m.group(1)), data)
                return _create_customer(conn, me, data)
            if method == "DELETE":
                guard = _csrf_ok(cookies, headers, data)
                if guard: return guard[:2]
                return _delete_customer(conn, me, path)
            return 405, {"ok": False, "error": "متد پشتیبانی نمی‌شود"}

        return 404, {"ok": False, "error": "مسیر API یافت نشد"}


# ------------------------------------------------------------ validators ----

def _validate_register(conn, data):
    username = (data or {}).get("username") or ""
    password = (data or {}).get("password") or ""
    if not _USERNAME_RE.match(username):
        return (400, {"ok": False, "error":
                      "نام کاربری: ۳ تا ۳۲ کاراکتر از حروف/عدد/._-"}, None)
    if len(password) < 6 or len(password) > 128:
        return (400, {"ok": False, "error": "رمز عبور باید ۶ کاراکتر یا بیشتر باشد"}, None)
    if conn.execute("SELECT 1 FROM users WHERE username = ?",
                    (username,)).fetchone():
        return (409, {"ok": False, "error": "این نام کاربری قبلاً ثبت شده است"}, None)
    return {"username": username, "pw": password}


# ---------------------------------------------------------------- admin ----

def _admin_route(conn, method, path, me, data):
    if method == "GET" and path == "admin/users":
        rows = conn.execute(
            "SELECT id, username, role, created FROM users ORDER BY id")
        return 200, {"ok": True, "users": [dict(r) for r in rows]}
    if method == "GET" and path == "admin/stats":
        users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        customers = conn.execute(
            "SELECT COUNT(*) AS c FROM customers").fetchone()["c"]
        return 200, {"ok": True, "stats": {"users": users,
                                           "customers": customers,
                                           "by_user": [dict(r) for r in
conn.execute(
    "SELECT u.username AS username, COUNT(c.id) AS n FROM users u "
    "LEFT JOIN customers c ON c.owner_id = u.id GROUP BY u.id")]}}
    return 404, {"ok": False, "error": "مسیر مدیریتی یافت نشد"}


# ----------------------------------------------------------------- CRUD ----

def _list_customers(conn, me):
    if me["role"] == "admin":
        rows = conn.execute(
            "SELECT c.*, u.username AS owner FROM customers c "
            "JOIN users u ON u.id = c.owner_id ORDER BY c.id DESC")
    else:
        rows = conn.execute(
            "SELECT c.*, u.username AS owner FROM customers c "
            "JOIN users u ON u.id = c.owner_id "
            "WHERE c.owner_id = ? ORDER BY c.id DESC", (me["id"],))
    return 200, {"ok": True, "customers": [dict(r) for r in rows]}


def _create_customer(conn, me, data):
    name = (data or {}).get("name") or ""
    phone = (data or {}).get("phone") or ""
    note = (data or {}).get("note") or ""
    if not (1 <= len(name) <= 80):
        return 400, {"ok": False, "error": "نام مشتری ۱ تا ۸۰ کاراکتر"}
    if phone and not _PHONE_RE.match(phone):
        return 400, {"ok": False, "error": "شماره تلفن نامعتبر است"}
    cur = conn.execute(
        "INSERT INTO customers (name, phone, note, owner_id, created) "
        "VALUES (?, ?, ?, ?, strftime('%s','now') * 1)",
        (name, phone, note[:300], me["id"]))
    row = conn.execute("SELECT * FROM customers WHERE id = ?",
                       (cur.lastrowid,)).fetchone()
    return 201, {"ok": True, "customer": dict(row)}


def _update_customer(conn, me, cid: int, data):
    row = conn.execute("SELECT * FROM customers WHERE id = ?",
                       (cid,)).fetchone()
    if row is None:
        return 404, {"ok": False, "error": "مشتری یافت نشد"}
    if me["role"] != "admin" and row["owner_id"] != me["id"]:
        return 403, {"ok": False, "error": "این رکورد مال شما نیست"}
    name = (data or {}).get("name") if data else None
    phone = (data or {}).get("phone") if data else None
    note = (data or {}).get("note") if data else None
    new_name = name if name is not None else row["name"]
    new_phone = phone if phone is not None else row["phone"]
    new_note = note if note is not None else row["note"]
    if not (1 <= len(new_name) <= 80):
        return 400, {"ok": False, "error": "نام مشتری ۱ تا ۸۰ کاراکتر"}
    if new_phone and not _PHONE_RE.match(new_phone):
        return 400, {"ok": False, "error": "شماره تلفن نامعتبر است"}
    conn.execute(
        "UPDATE customers SET name = ?, phone = ?, note = ? WHERE id = ?",
        (new_name, new_phone, new_note[:300], cid))
    row = conn.execute("SELECT * FROM customers WHERE id = ?",
                       (cid,)).fetchone()
    return 200, {"ok": True, "customer": dict(row)}


def _delete_customer(conn, me, path):
    m = re.match(r"^customers/(\d+)$", path)
    if not m:
        return 400, {"ok": False, "error": "شناسه مشتری نامعتبر است"}
    cid = int(m.group(1))
    row = conn.execute("SELECT owner_id FROM customers WHERE id = ?",
                       (cid,)).fetchone()
    if row is None:
        return 404, {"ok": False, "error": "مشتری یافت نشد"}
    if me["role"] != "admin" and row["owner_id"] != me["id"]:
        return 403, {"ok": False, "error": "این رکورد مال شما نیست"}
    conn.execute("DELETE FROM customers WHERE id = ?", (cid,))
    return 200, {"ok": True, "deleted": cid}
