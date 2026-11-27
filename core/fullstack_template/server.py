"""{{APP_TITLE}} — فول‌استک واقعی روی Termux: بک‌اند stdlib + SQLite.

اجرا:  python server.py
  پورت از env AI_FACTORY_PORT یا PORT (پیش‌فرض 8770)
  میزبان از env AI_FACTORY_HOST یا HOST (پیش‌فرض امن 127.0.0.1)
یا در AI Factory: زبانه «اجرا» در /factory.html
آزمون: python tests/test_api.py
"""

import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from app.api import dispatch  # noqa: E402
from app.db import init_db    # noqa: E402

UI_DIR = os.path.join(ROOT, "ui")
APP_HOST = (os.environ.get("AI_FACTORY_HOST")
            or os.environ.get("HOST") or "").strip() or "127.0.0.1"
APP_PORT = int(os.environ.get("AI_FACTORY_PORT")
               or os.environ.get("PORT") or "8770")

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/user.html": ("user.html", "text/html; charset=utf-8"),
    "/admin.html": ("admin.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}

MAX_BODY = 200_000


class Handler(BaseHTTPRequestHandler):
    server_version = "{{APP_ID}}/1.0"

    # ---------- helpers ----------
    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8",
              headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or []):
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json(self, code, payload, headers=None):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode(),
                   headers=headers)

    def _cookies(self):
        out = {}
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def _read_body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if n <= 0 or n > MAX_BODY:
            return None
        raw = self.rfile.read(n)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else None
        except (ValueError, UnicodeDecodeError):
            return None

    # ---------- routing ----------
    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            api_path = path[len("/api/"):]
            if api_path.startswith("static/"):     # never expose fs
                return self._json(404, {"ok": False, "error": "یافت نشد"})
            try:
                result = dispatch("GET", api_path, None, self._cookies(),
                                  dict(self.headers.items()))
            except Exception:                        # noqa: BLE001
                return self._json(500, {"ok": False, "error": "خطای داخلی"})
            return self._emit(result)
        static = STATIC.get(path)
        if static and os.path.isfile(os.path.join(UI_DIR, static[0])):
            fp = os.path.join(UI_DIR, static[0])
            with open(fp, "rb") as f:
                self._send(200, f.read(), ctype=static[1])
            return
        return self._json(404, {"ok": False, "error": "یافت نشد"})

    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if not path.startswith("/api/"):
            return self._json(404, {"ok": False, "error": "یافت نشد"})
        api_path = path[len("/api/"):]
        data = self._read_body()
        if data is None:
            return self._json(400, {"ok": False, "error": "بدنه نامعتبر است"})
        try:
            result = dispatch("POST", api_path, data, self._cookies(),
                              dict(self.headers.items()))
        except Exception:                            # noqa: BLE001
            return self._json(500, {"ok": False, "error": "خطای داخلی"})
        return self._emit(result)

    def do_DELETE(self):
        parsed = urllib.parse.urlsplit(self.path)
        if not parsed.path.startswith("/api/"):
            return self._json(404, {"ok": False, "error": "یافت نشد"})
        data = self._read_body()
        try:
            result = dispatch("DELETE", parsed.path[len("/api/"):], data,
                              self._cookies(), dict(self.headers.items()))
        except Exception:                            # noqa: BLE001
            return self._json(500, {"ok": False, "error": "خطای داخلی"})
        return self._emit(result)

    def _emit(self, result):
        """dispatch returns (code, payload[, set_cookies[, headers]]) or a
        bare payload. Normalize every shape without losing Set-Cookie."""
        code, payload, set_cookies, headers = 200, result, [], []
        if isinstance(result, tuple):
            if len(result) == 4:
                code, payload, set_cookies, headers = result
            elif len(result) == 3:
                code, payload, set_cookies = result
            elif len(result) == 2:
                code, payload = result
        hs = list(headers or [])
        for c in set_cookies or []:
            hs.append(("Set-Cookie", c))
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, body, headers=hs)

    def log_message(self, *a):   # keep run logs clean
        pass


def main():
    if os.environ.get("AF_DB_DIR"):        # isolated db dir (tests)
        os.environ["AF_FORCE_DB"] = os.path.join(
            os.environ["AF_DB_DIR"], "app.db")
    init_db()
    print("{{APP_TITLE}} listening on http://%s:%d"
          % (APP_HOST, APP_PORT), flush=True)
    ThreadingHTTPServer((APP_HOST, APP_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
