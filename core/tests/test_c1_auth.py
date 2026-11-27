"""C1 — real HTTP reproduction: every mutation route must be auth-gated.

This is NOT a source-text check.  It boots the real `Handler` on an ephemeral
loopback port against the isolated test root and issues real HTTP requests.
"""
import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer

import core.config as config
from core import panelauth, server


PROJECT = "c1_trust_probe"
PASSWORD = "AF26-Strong-Passphrase-123"


class MutationAuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        d = config.PROJECTS_DIR / PROJECT
        d.mkdir(parents=True, exist_ok=True)
        cls.target = d / "index.html"
        cls.original = "<h1>original</h1>"
        cls.target.write_text(cls.original, encoding="utf-8")

        panelauth.init(PASSWORD)
        cls.session, cls.csrf = panelauth.login(PASSWORD)

        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.httpd.server_address[1]

    def setUp(self):
        # Each test starts from the same known state; no order dependencies.
        self.target.write_text(self.original, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def _request(self, method, path, body=None, cookies="", csrf=""):
        headers = {}
        if cookies:
            headers["Cookie"] = cookies
        if csrf:
            headers["X-Panel-CSRF"] = csrf
        raw = json.dumps(body).encode("utf-8") if body is not None else None
        if raw is not None:
            headers["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body=raw, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        try:
            parsed = json.loads(data) if data else {}
        except ValueError:
            parsed = {"raw": data[:200].decode("utf-8", "replace")}
        return resp.status, parsed

    def _put_body(self, content):
        return {"project": PROJECT, "path": "index.html", "content": content}

    def test_put_without_session_is_denied_and_does_not_write(self):
        status, body = self._request("PUT", "/api/file",
                                     self._put_body("HACKED-NO-AUTH"))
        self.assertEqual(status, 401, body)
        self.assertEqual(self.target.read_text(encoding="utf-8"), self.original,
                         "unauthenticated PUT modified project source")

    def test_put_with_valid_session_but_wrong_csrf_is_denied(self):
        cookies = ("%s=%s; %s=WRONG" %
                   (panelauth.SESSION_COOKIE, self.session, panelauth.CSRF_COOKIE))
        status, body = self._request("PUT", "/api/file",
                                     self._put_body("HACKED-BAD-CSRF"),
                                     cookies=cookies, csrf="WRONG")
        self.assertEqual(status, 403, body)
        self.assertEqual(self.target.read_text(encoding="utf-8"), self.original)

    def test_put_with_valid_session_and_csrf_writes_real_file(self):
        cookies = ("%s=%s; %s=%s" %
                   (panelauth.SESSION_COOKIE, self.session,
                    panelauth.CSRF_COOKIE, self.csrf))
        status, body = self._request("PUT", "/api/file",
                                     self._put_body("<h1>authorized</h1>"),
                                     cookies=cookies, csrf=self.csrf)
        self.assertEqual(status, 200, body)
        self.assertEqual(self.target.read_text(encoding="utf-8"),
                         "<h1>authorized</h1>")

    def test_delete_without_session_is_denied(self):
        status, body = self._request("DELETE", "/api/file?project=%s&path=index.html" % PROJECT)
        self.assertEqual(status, 401, body)
        self.assertTrue(self.target.is_file())

    def test_public_health_still_works(self):
        status, body = self._request("GET", "/api/health")
        self.assertEqual(status, 200, body)
        self.assertTrue(body.get("ok"))


if __name__ == "__main__":
    unittest.main()
