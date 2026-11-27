"""test_aiclient — ۱۰ آزمون واقعی برای ماژول مستقل اتصال هوش مصنوعی.

سرور آزمایشی OpenAI-سازگار محلی در همین فایل بالا می‌آید (port 0 واقعی).
هیچ سرویس پرداختی، هیچ شبکه بیرونی، هیچ کلید واقعی — فقط رشته‌های ساختگی.
اجرا:  python tests/test_aiclient.py   (یعنی ۱۰ سناریو)
"""

import importlib.util
import json
import os
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app" / "aiclient.py"
SPEC = importlib.util.spec_from_file_location("aiclient", APP)
aiclient = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(aiclient)

KEY = "sk-mock-key-DO-NOT-TRUST"          # کلید ساختگی فقط برای آزمون
GOOD_REPLY = "سلام — پاسخ واقعی از سرور آزمایشی"


class MockOpenAI:
    """سرور آزمایشی سازگار OpenAI با مودهای: ok | limited | slow | badjson."""

    def __init__(self, mode="ok"):
        self.mode = mode
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _emit(self, code, payload):
                body = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_AUTH(self):                      # shared logic
                auth = self.headers.get("Authorization") or ""
                if auth != "Bearer " + KEY:
                    return self._emit(401, {"error": "invalid key"})
                if outer.mode == "limited":
                    self.send_response(429)
                    self.send_header("Retry-After", "2")
                    body = b'{"error":"rate limited"}'
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    return self.wfile.write(body)
                if outer.mode == "slow":
                    import time
                    time.sleep(6)
                    return self._emit(200, {"choices": [{"message":
                        {"content": GOOD_REPLY}}]})
                if outer.mode == "badjson":
                    body = b'sure-this-is-not-json{{'
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    return self.wfile.write(body)
                if outer.mode != "ok":
                    return self._emit(500, {"error": "mode"})
                self._emit(200, {"choices": [{"message": {"content": GOOD_REPLY}}],
                                 "model": "mock"})

            def do_POST(self):
                pathLen = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(pathLen)
                if self.path.endswith("/chat/completions"):
                    self.do_AUTH()
                    return
                self._emit(404, {"error": "nf"})

            def do_GET(self):
                if self.path.endswith("/models"):
                    if (self.headers.get("Authorization") or "") != "Bearer " + KEY:
                        return self._emit(401, {"error": "invalid key"})
                    return self._emit(200, {"data": [{"id": "mock"}]})
                self._emit(404, {"error": "nf"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self):
        return "http://127.0.0.1:%d/v1" % self.port

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Base(unittest.TestCase):
    def setUp(self):
        self._clean = {}
        for k in ("AI_PROVIDER", "AI_BASE_URL", "AI_MODEL", "AI_API_KEY",
                  "AI_API_KEY_FILE", "AI_TIMEOUT", "AI_CONFIG_FILE"):
            self._clean[k] = os.environ.pop(k, None)
        self.cfgdir = Path(__file__).resolve().parent.parent
        os.environ["AI_CONFIG_FILE"] = str(self.cfgdir)   # no config.json there
        self.mock = None

    def tearDown(self):
        if self.mock:
            self.mock.stop()
        for k, v in self._clean.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def _ok_server(self):
        self.mock = MockOpenAI("ok")
        os.environ["AI_BASE_URL"] = self.mock.base
        os.environ["AI_MODEL"] = "mock"
        os.environ["AI_API_KEY"] = KEY
        os.environ["AI_TIMEOUT"] = "8"


class HappyPath(Base):
    def test_1_success_reply_and_ready_status(self):
        self._ok_server()
        out = aiclient.send([{"role": "user", "content": "سلام"}])
        self.assertEqual(out, GOOD_REPLY)
        st = aiclient.status()
        self.assertEqual(st["provider"], "openai")
        self.assertTrue(st["ready"])
        self.assertEqual(st["key"], "set")

    def test_2_empty_messages_rejected(self):
        self._ok_server()
        with self.assertRaises(aiclient.AiError):
            aiclient.send([])


class FailureModes(Base):
    def test_3_missing_settings_clear_error(self):
        # هیچ تنظیماتی: نه env، نه config
        with self.assertRaises(aiclient.AiError) as cm:
            aiclient.send([{"role": "user", "content": "سلام"}])
        self.assertEqual(cm.exception.kind, "no_settings")
        st = aiclient.status()
        self.assertFalse(st["ready"])
        self.assertEqual(st["key"], "unset")

    def test_4_invalid_key_is_auth(self):
        self._ok_server()
        os.environ["AI_API_KEY"] = "WRONG-KEY"
        with self.assertRaises(aiclient.AiError) as cm:
            aiclient.send([{"role": "user", "content": "سلام"}])
        self.assertEqual(cm.exception.kind, "auth")

    def test_5_unreachable_address_is_network(self):
        # پورت حتماً بسته: bind کرده و می‌بندیم
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        os.environ["AI_BASE_URL"] = "http://127.0.0.1:%d/v1" % port
        os.environ["AI_MODEL"] = "mock"
        os.environ["AI_API_KEY"] = KEY
        with self.assertRaises(aiclient.AiError) as cm:
            aiclient.send([{"role": "user", "content": "سلام"}], timeout=3)
        self.assertEqual(cm.exception.kind, "network")

    def test_6_timeout_strict(self):
        self.mock = MockOpenAI("slow")
        os.environ["AI_BASE_URL"] = self.mock.base
        os.environ["AI_MODEL"] = "mock"
        os.environ["AI_API_KEY"] = KEY
        with self.assertRaises(aiclient.AiError) as cm:
            aiclient.send([{"role": "user", "content": "سلام"}], timeout=1.2)
        self.assertEqual(cm.exception.kind, "timeout")

    def test_7_rate_limit_429(self):
        self.mock = MockOpenAI("limited")
        os.environ["AI_BASE_URL"] = self.mock.base
        os.environ["AI_MODEL"] = "mock"
        os.environ["AI_API_KEY"] = KEY
        with self.assertRaises(aiclient.AiError) as cm:
            aiclient.send([{"role": "user", "content": "سلام"}])
        self.assertEqual(cm.exception.kind, "rate_limit")
        self.assertIn("تلاش مجدد", str(cm.exception))

    def test_8_bad_json_response(self):
        self.mock = MockOpenAI("badjson")
        os.environ["AI_BASE_URL"] = self.mock.base
        os.environ["AI_MODEL"] = "mock"
        os.environ["AI_API_KEY"] = KEY
        with self.assertRaises(aiclient.AiError) as cm:
            aiclient.send([{"role": "user", "content": "سلام"}])
        self.assertEqual(cm.exception.kind, "bad_response")


class SafetyAndIndependence(Base):
    def test_9_secrets_never_leak_to_browser_view(self):
        self._ok_server()
        samples = [str(aiclient.status()),
                   json.dumps(aiclient.status(), ensure_ascii=False),
                   json.dumps(aiclient.describe_settings(), ensure_ascii=False)]
        try:
            aiclient.send([{"role": "user", "content": "سلام"}])
        except aiclient.AiError as e:
            samples.append(str(e))
        for s in samples:
            self.assertNotIn(KEY, s, "secret leaked")
        # استثنای کلید غلط هم کلید را نمی‌گوید
        os.environ["AI_API_KEY"] = "OTHER-" + KEY[::-1]
        try:
            aiclient.send([{"role": "user", "content": "سلام"}])
        except aiclient.AiError as e:
            self.assertNotIn(KEY, str(e))
            self.assertNotIn(os.environ["AI_API_KEY"], str(e))

    def test_10_no_opencode_no_aifactory_deps(self):
        src = APP.read_text(encoding="utf-8")
        # ۱) فقط importهای کتابخانه استاندارد مجازند:
        import ast
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module.split(".")[0])
        allowed = {"json", "os", "time", "urllib", "pathlib"}
        self.assertTrue(imported.issubset(allowed),
                        "واردات خارج از استاندارد: %s" %
                        sorted(imported - allowed))
        # ۲) هیچ رشته وابستگی محیطی به فابریک (case-sensitive) وجود ندارد:
        for bad in ("8580", "4556", "OPENCODE_SERVER_PASSWORD",
                    "OPENCODE_TOKEN", "ai-factory-test"):
            self.assertNotIn(bad, src)

    def test_11_pluggable_provider_without_logic_change(self):
        provs = aiclient.register_provider("mock", send=lambda cfg, msgs, **kw:
                                           "پاسخ موک",
                                           ping=lambda cfg: {"ready": True,
                                                             "detail": "mock"})
        os.environ["AI_PROVIDER"] = "mock"
        os.environ.pop("AI_BASE_URL", None)
        os.environ.pop("AI_API_KEY", None)
        self.assertEqual(aiclient.send([{"role": "user", "content": "x"}]),
                         "پاسخ موک")
        st = aiclient.status()
        self.assertEqual(st["provider"], "mock")
        self.assertIn("mock", provs)

    def test_12_project_without_ai_still_no_error_on_status(self):
        # بدون env و بدون config — وضعیت صادقانه نه کرش
        st = aiclient.status()
        self.assertFalse(st["ready"])
        self.assertEqual(st["provider"], "openai")
        d = aiclient.describe_settings()
        self.assertNotIn("sk-", json.dumps(d))


if __name__ == "__main__":
    import json as _json
    res = unittest.main(argv=[sys.argv[0]], exit=False, verbosity=2)
    total = res.result.testsRun
    bad = len(res.result.failures) + len(res.result.errors)
    print("═══ نتیجه: %d آزمون، %d موفق، %d ناموفق ═══" % (total, total - bad, bad))
    sys.exit(0 if bad == 0 else 1)
