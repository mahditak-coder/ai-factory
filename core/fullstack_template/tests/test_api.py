"""Self-tests for {{APP_TITLE}} — REAL end-to-end against a real server.

Run:  python tests/test_api.py
The test boots the actual server.py on a free localhost port with an
isolated temporary SQLite DB, then verifies the full flow:
register → first user becomes admin → sessions/cookies → admin
protection → CRUD → persistence across restart → CSRF → validation.
Stdlib only.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import http.cookiejar
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server.py"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Client:
    """Tiny cookie-aware HTTP client for the app under test."""

    def __init__(self, base):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def _cookie_header(self):
        return "; ".join("%s=%s" % (c.name, c.value) for c in self.jar)

    def csrf(self):
        with self.opener.open(self.base + "/api/csrf", timeout=15) as r:
            pass
        top_key = "af_csrf"
        for c in self.jar:
            if c.name == top_key:
                return c.value
        return ""

    def call(self, method, path, body=None, csrf=None):
        headers = {}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        data = json.dumps(body).encode() if body is not None else None
        if data:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + "/api/" + path, method=method,
                                     data=data, headers=headers)
        try:
            with self.opener.open(req, timeout=25) as resp:
                code = resp.status
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            code = e.code
            payload = json.loads(e.read().decode() or "{}")
        return code, payload

    def call_bad(self, method, path, body=None, no_csrf=False):
        """Call with a WRONG csrf token (security failure test)."""
        headers = {"X-CSRF-Token": ("0" * 64) if not no_csrf else ""}
        data = json.dumps(body).encode() if body is not None else None
        if data:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + "/api/" + path, method=method,
                                     data=data, headers=headers)
        try:
            with self.opener.open(req, timeout=25) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")


class FullFlow(unittest.TestCase):
    proc = None
    port = None
    db_dir = None
    cl = None
    base = None

    @classmethod
    def _start_server(cls):
        env = dict(os.environ)
        env["AI_FACTORY_PORT"] = str(cls.port)
        env["AF_DB_DIR"] = str(cls.db_dir)
        cls.proc = subprocess.Popen(
            [sys.executable, str(SERVER)], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=str(ROOT))
        for _ in range(50):
            try:
                s = socket.create_connection(("127.0.0.1", cls.port), 1)
                s.close()
                return
            except OSError:
                time.sleep(0.3)
        raise RuntimeError("server did not start")

    @classmethod
    def setUpClass(cls):
        cls.db_dir = tempfile.mkdtemp(prefix="fullstack-test-")
        cls.port = free_port()
        cls.base = "http://127.0.0.1:%d" % cls.port
        cls._start_server()
        cls.cl = Client(cls.base)

    @classmethod
    def tearDownClass(cls):
        if cls.proc:
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
        subprocess.run(["pkill", "-f", "AF_DB_DIR=%s" % cls.db_dir],
                       capture_output=True)

    # ------------------------------------------------------------ scenarios

    def test_01_bootstrap_csrf(self):
        token = self.cl.csrf()
        self.assertRegex(token, r"^[0-9a-f]{64}$")
        FullFlow.csrf = token

    def test_02_register_first_user_becomes_admin(self):
        code, d = self.cl.call("POST", "auth/register", {
            "username": "boss", "password": "secret123", "csrf_token": FullFlow.csrf})
        self.assertEqual(201, code)
        self.assertEqual("admin", d["user"]["role"])

    def test_03_me_after_register(self):
        code, d = self.cl.call("GET", "me")
        self.assertEqual(200, code)
        self.assertEqual("boss", d["user"]["username"])

    def test_04_second_user_is_plain_user(self):
        # a SEPARATE client registers; self.cl must remain the 'boss' session
        cl2 = Client(self.base)
        csrf = cl2.csrf()
        code, d = cl2.call("POST", "auth/register", {
            "username": "worker", "password": "goodpass1", "csrf_token": csrf})
        self.assertEqual(201, code)
        self.assertEqual("user", d["user"]["role"])
        code, d = self.cl.call("GET", "me")   # boss still logged in
        self.assertEqual(200, code)

    def test_05_admin_area_allows_admin(self):
        code, d = self.cl.call("GET", "admin/users")
        self.assertEqual(200, code)
        self.assertTrue(any(u["role"] == "admin" for u in d["users"]))

    def test_06_admin_area_blocks_user_role(self):
        # switch cookie to the 'worker' session: login fresh client
        cl2 = Client(self.base)
        csrf = cl2.csrf()
        code, _ = cl2.call("POST", "auth/login", {
            "username": "worker", "password": "goodpass1", "csrf_token": csrf})
        self.assertEqual(200, code)
        code, d = cl2.call("GET", "admin/users")
        self.assertEqual(403, code)
        self.assertIn("مدیر", d["error"])

    def test_07_admin_stats(self):
        code, d = self.cl.call("GET", "admin/stats")
        self.assertEqual(200, code)
        self.assertEqual(2, d["stats"]["users"])

    def test_08_wrong_password_login_fails(self):
        cl2 = Client(self.base)
        csrf = cl2.csrf()
        code, d = cl2.call("POST", "auth/login", {
            "username": "boss", "password": "WRONG", "csrf_token": csrf})
        self.assertEqual(401, code)

    def test_09_unknown_user_login_fails(self):
        cl2 = Client(self.base)
        csrf = cl2.csrf()
        code, _ = cl2.call("POST", "auth/login", {
            "username": "nobody", "password": "whatever1", "csrf_token": csrf})
        self.assertEqual(401, code)

    def test_10_validation_bad_username(self):
        csrf = self.cl.csrf()
        code, d = self.cl.call("POST", "auth/register", {
            "username": "x", "password": "longenough1", "csrf_token": csrf})
        self.assertEqual(400, code)

    def test_11_validation_short_password(self):
        csrf = self.cl.csrf()
        code, d = self.cl.call("POST", "auth/register", {
            "username": "someone", "password": "123", "csrf_token": csrf})
        self.assertEqual(400, code)

    def test_12_admin_customer_crud_real_db(self):
        csrf = self.cl.csrf()   # fresh token (cookie may have rotated)
        # create
        code, d = self.cl.call("POST", "customers", {
            "name": "شرکت داده‌گستر", "phone": "021-4455", "note": "پروژه X",
            "csrf_token": csrf})
        self.assertEqual(201, code)
        cust = d["customer"]
        self.assertEqual("شرکت داده‌گستر", cust["name"])
        # list
        code, d = self.cl.call("GET", "customers")
        self.assertEqual(200, code)
        self.assertTrue(any(c["id"] == cust["id"] for c in d["customers"]))
        # update
        code, d = self.cl.call("POST", "customers/%d/update" % cust["id"], {
            "name": "شرکت داده‌گستر نوین", "csrf_token": csrf})
        self.assertEqual(200, code)
        self.assertEqual("شرکت داده‌گستر نوین", d["customer"]["name"])
        # delete
        code, d = self.cl.call("DELETE", "customers/%d" % cust["id"], {
            "csrf_token": csrf})
        self.assertEqual(200, code)
        code, d = self.cl.call("GET", "customers")
        self.assertFalse(any(c["id"] == cust["id"] for c in d["customers"]))

    def test_13_user_owns_own_records_only(self):
        # worker creates a customer; admin can see all
        boss_csrf = self.cl.csrf()   # fresh admin token
        cl2 = Client(self.base)
        csrf = cl2.csrf()
        cl2.call("POST", "auth/login", {
            "username": "worker", "password": "goodpass1", "csrf_token": csrf})
        code, d = cl2.call("POST", "customers", {
            "name": "کافه داخل کوچه", "phone": "", "csrf_token": csrf})
        self.assertEqual(201, code)
        wid = d["customer"]["id"]
        boss_code, d = self.cl.call("GET", "customers")   # admin sees all
        self.assertTrue(any(c["id"] == wid for c in d["customers"]))
        # worker can NOT see boss' record if boss creates one (admin sees it,
        # worker's list contains ONLY own rows)
        code, d = self.cl.call("POST", "customers", {
            "name": "شرکت محرمانه", "csrf_token": boss_csrf})
        boss_id = d["customer"]["id"]
        code, d = cl2.call("GET", "customers")
        names = [c["name"] for c in d["customers"]]
        self.assertIn("کافه داخل کوچه", names)
        self.assertNotIn("شرکت محرمانه", names)
        # worker cannot update/delete boss' record
        code, _ = cl2.call("POST", "customers/%d/update" % boss_id, {
            "name": "هک", "csrf_token": csrf if csrf else cl2.csrf()})
        self.assertEqual(403, code)
        # cleanup boss record
        self.cl.call("DELETE", "customers/%d" % boss_id, {"csrf_token": boss_csrf})
        self.cl.call("DELETE", "customers/%d" % wid, {"csrf_token": boss_csrf})
        cl2.call("POST", "auth/logout", {}, csrf)

    def test_14_csrf_blocks_mutations(self):
        code, d = self.cl.call_bad("POST", "customers", {
            "name": "بدون csrf"})
        self.assertEqual(403, code)
        self.assertIn("CSRF", d["error"])
        code, d = self.cl.call_bad("POST", "auth/login", {
            "username": "boss", "password": "secret123"})
        self.assertEqual(403, code)

    def test_15_logout_and_me_401(self):
        cl2 = Client(self.base)
        csrf = cl2.csrf()
        cl2.call("POST", "auth/login", {
            "username": "worker", "password": "goodpass1", "csrf_token": csrf})
        code, d = cl2.call("GET", "me")
        self.assertEqual(200, code)
        code, d = cl2.call("POST", "auth/logout", {}, csrf)
        self.assertEqual(200, code)
        code, _ = cl2.call("GET", "me")
        self.assertEqual(401, code)

    def test_16_persistence_across_restart(self):
        csrf = self.cl.csrf()
        code, d = self.cl.call("POST", "customers", {
            "name": "مشتریِ دوام", "phone": "021-911", "csrf_token": csrf})
        self.assertEqual(201, code)
        cid = d["customer"]["id"]
        # kill & restart the REAL server, same DB dir
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        time.sleep(0.5)
        FullFlow._start_server()
        cl2 = Client(self.base)
        csrf = cl2.csrf()
        code, d = cl2.call("POST", "auth/login", {
            "username": "boss", "password": "secret123", "csrf_token": csrf})
        self.assertEqual(200, code, "login must work after restart")
        code, d = cl2.call("GET", "customers")
        self.assertTrue(any(c["id"] == cid for c in d["customers"]),
                        "record must survive restart")
        cl2.call("DELETE", "customers/%d" % cid, {"csrf_token": csrf})

    def test_17_unknown_api_route_404(self):
        code, _ = self.cl.call("GET", "definitely/not/here")
        self.assertEqual(404, code)

    def test_18_index_page_served(self):
        with urllib.request.urlopen(self.base + "/", timeout=15) as r:
            body = r.read().decode("utf-8")
        self.assertIn("<html", body.lower())
        self.assertIn("<!doctype", body[:200].lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
