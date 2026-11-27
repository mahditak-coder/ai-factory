"""FREE OpenRouter image provider — mocked tests, zero real network/cost."""
import base64, io, json, os, tempfile, unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import core.config as config
from core import image_governance as gov, image_providers as ip, image_remote as ir
from core import paths

MODEL = "recraft/recraft-v4.1-flash-v2:free"
BUF = io.BytesIO(); Image.new("RGBA", (64, 64), (200, 100, 50, 255)).save(BUF, "PNG")
PNG = BUF.getvalue()
B64 = base64.b64encode(PNG).decode()
JBUF = io.BytesIO(); Image.new("RGB", (48, 48), (210, 120, 60)).save(JBUF, "JPEG")
JPEG = JBUF.getvalue()
JB64 = base64.b64encode(JPEG).decode()


class OpenRouterTest(unittest.TestCase):
    def setUp(self):
        d = config.PROJECTS_DIR / "or-image-unit"
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text('{"name":"or-image-unit","type":"webapp"}')
        self.store = tempfile.NamedTemporaryFile(suffix=".json")
        Path(self.store.name).write_text("{}", encoding="utf-8")
        self.cfg = tempfile.NamedTemporaryFile(suffix=".json")
        Path(self.cfg.name).write_text(json.dumps({
            "spending": {"per_request_usd": 1.0, "daily_usd": 5.0},
            "providers": [{"id": "openrouter-image", "free_cost_model": True,
                           "models": {MODEL: {"estimated_cost_usd": 0.0}}}]}),
            encoding="utf-8")
        self.ps = mock.patch.object(gov, "_STORE", Path(self.store.name))
        self.pc = mock.patch.object(gov, "_CONFIG", Path(self.cfg.name))
        self.ps.start(); self.pc.start()
        self.calls = []

    def tearDown(self):
        self.ps.stop(); self.pc.stop(); self.store.close(); self.cfg.close()

    def http_ok(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body))
        payload = {"created": 1, "data": [{"b64_json": B64, "media_type": "image/png"}],
                   "usage": {"cost": 0, "total_tokens": 10}}
        return 200, {}, json.dumps(payload).encode()

    def approve(self, **extra):
        req = {"provider": "openrouter-image", "model": MODEL,
               "quality": "standard", "prompt": "professional fox illustration",
               "size": "1024x1024", "references": [], "operation": "generate",
               "project": "or-image-unit"}
        req.update(extra)
        a = gov.request_approval("or-image-unit", gov.payload_from("or-image-unit", req),
                                 "tester")
        self.assertEqual(a["status"], "PASS", a)
        self.assertEqual(a["estimated_cost_usd"], 0.0)
        self.assertEqual(gov.decide(a["approval_id"], "approve", "tester")["status"],
                         "PASS")
        req["approval_id"] = a["approval_id"]
        req["confirmed_cost_usd"] = "0"
        return req

    def run_provider(self, req, http=None):
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-only"}), \
             mock.patch.object(ir, "_http", http or self.http_ok), \
             mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("REAL NETWORK")):
            return ip.get("openrouter-image").generate(req.pop("prompt"), **req)

    def test_registration_and_exact_model(self):
        p = ip.get("openrouter-image")
        self.assertIsNotNone(p)
        self.assertEqual(p.models, (MODEL,))
        st = p.status()
        self.assertEqual(st["credential_env"], "OPENROUTER_API_KEY")
        self.assertFalse(st["supports_editing"])
        self.assertFalse(st["supports_reference_images"])
        self.assertIn("BLOCKED", st["eu_routing"])
        self.assertEqual(st["endpoint"], "https://openrouter.ai/api/v1/images")

    def test_success_saves_real_png_with_zero_billing(self):
        req = self.approve()
        r = self.run_provider(req)
        self.assertEqual(r.status, "PASS", r.error)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1], "https://openrouter.ai/api/v1/images")
        body = self.calls[0][3]
        if isinstance(body, (str, bytes)):
            body = json.loads(body)
        self.assertEqual(body["model"], MODEL)
        self.assertEqual(body["n"], 1)
        self.assertEqual(body["response_format"], "b64_json")
        meta = json.loads((paths.project_dir("or-image-unit") /
                           (r.path + ".json")).read_text())
        self.assertEqual(meta["billing_cost_usd"], 0)
        self.assertTrue(meta["billing_verified"])
        self.assertEqual(meta["model"], MODEL)
        self.assertEqual(r.sha256, meta["sha256"])

    def test_no_key_and_no_real_network(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(ir, "_http", side_effect=AssertionError("NETWORK")), \
             mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("REAL NETWORK")):
            r = ip.get("openrouter-image").generate("x", model=MODEL,
                        project="or-image-unit", approval_id="none",
                        confirmed_cost_usd="0")
            self.assertEqual(r.status, "BLOCKED")
            self.assertIn("missing-credential", r.error)
            self.assertEqual(self.calls, [])

    def test_http_error_matrix_and_eu_routing(self):
        cases = [(401, b'{"error":{"message":"bad key"}}', "authentication-failed"),
                 (403, b'{"error":{"message":"spend limit"}}', "access-blocked"),
                 (429, b'{"error":{"message":"slow down"}}', "rate-limited"),
                 (503, b'{"error":{"message":"upstream"}}', "upstream-error"),
                 (403, b'{"error":{"message":"EU in-region routing unavailable"}}',
                  "regional-routing-unavailable")]
        for code, raw, expected in cases:
            req = self.approve()
            with self.subTest(code=code, expected=expected):
                r = self.run_provider(req, lambda *a, c=code, r=raw: (c, {}, r))
                self.assertEqual(r.status, "BLOCKED" if "routing" in expected else "FAIL")
                self.assertIn(expected, r.error)

    def test_malformed_responses_and_no_url_fallback(self):
        for raw, expected in ((b"not-json", "malformed-json"),
                              (json.dumps({"data": [{"b64_json": "!!!"}]}).encode(),
                               "malformed-base64"),
                              (json.dumps({"data": [{"url": "https://x/y.png"}]}).encode(),
                               "no-inline-b64"),
                              (json.dumps({"data": [{"b64_json": B64}],
                                           "usage": {"cost": 0.04}}).encode(),
                               "unexpected-nonzero-charge")):
            req = self.approve()
            r = self.run_provider(req, lambda *a, r=raw: (200, {}, r))
            self.assertNotEqual(r.status, "PASS")
            self.assertIn(expected, r.error)

    def test_jpeg_response_is_safely_converted_to_png(self):
        req = self.approve()
        def http(method, url, headers, body, timeout):
            self.calls.append((method, url, headers, body))
            payload = {"data": [{"b64_json": JB64, "media_type": "image/jpeg"}],
                       "usage": {"cost": 0}}
            return 200, {}, json.dumps(payload).encode()
        r = self.run_provider(req, http)
        self.assertEqual(r.status, "PASS", r.error)
        meta = json.loads((paths.project_dir("or-image-unit") /
                           (r.path + ".json")).read_text())
        self.assertEqual(meta["response_diagnostics"]["converted_from"], "JPEG")
        out = paths.project_dir("or-image-unit") / r.path
        self.assertTrue(out.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(out) as im:
            self.assertEqual(im.format, "PNG")
            self.assertEqual(im.size, (48, 48))

    def test_text_response_is_rejected_with_diagnostics(self):
        req = self.approve()
        text = b'{"error":{"message":"model busy"}}'
        def http(method, url, headers, body, timeout):
            self.calls.append((method, url, headers, body))
            payload = {"data": [{"b64_json": base64.b64encode(text).decode(),
                                 "media_type": "application/json"}],
                       "usage": {"cost": 0}}
            return 200, {}, json.dumps(payload).encode()
        r = self.run_provider(req, http)
        self.assertEqual(r.status, "FAIL")
        self.assertIn("not-a-decodable-image", r.error)
        self.assertIn("application/json", json.dumps(r.evidence))
        self.assertFalse((paths.project_dir("or-image-unit") /
                          "assets/generated-tests/dana-openrouter-free-test.png").exists())

    def test_references_edit_paid_model_and_no_fallback(self):
        paid = self.approve()
        paid["model"] = "openai/gpt-4o"   # tamper after approval -> reject at provider
        r = self.run_provider(paid)
        self.assertEqual(r.status, "BLOCKED")
        self.assertIn("non-free", r.error)
        self.assertEqual(self.calls, [])
        refs = self.approve(references=["https://example.test/ref.png"])
        r = self.run_provider(refs)
        self.assertEqual(r.status, "BLOCKED")
        self.assertIn("reference-images-not-supported", r.error)
        edit = self.approve(operation="edit")  # approved, but provider rejects editing
        r = self.run_provider(edit)
        self.assertEqual(r.status, "BLOCKED")
        self.assertIn("editing-not-supported", r.error)
        p = ip.get("openrouter-image")
        self.assertEqual(p.edit({}).status, "BLOCKED")
        self.assertEqual(self.calls, [])  # no fallback call to any provider

    def test_approval_hash_mismatch_and_zero_cost_governance(self):
        est = gov.estimate("openrouter-image", MODEL)
        self.assertTrue(est["ok"])
        self.assertEqual(est["amount_usd"], 0.0)
        self.assertTrue(est["free"])
        bad = self.approve()
        bad["prompt"] = "TAMPERED"
        r = self.run_provider(bad)
        self.assertEqual(r.status, "BLOCKED")
        self.assertIn("approval-request-mismatch", r.error)
        self.assertEqual(self.calls, [])
        cfg = json.loads(Path(self.cfg.name).read_text())
        cfg["providers"][0]["models"][MODEL]["estimated_cost_usd"] = 1.5
        Path(self.cfg.name).write_text(json.dumps(cfg))
        blocked = gov.request_approval("or-image-unit", gov.payload_from(
            "or-image-unit", {"provider": "openrouter-image", "model": MODEL,
                              "prompt": "x", "operation": "generate"}), "tester")
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertIn("per-request-limit-exceeded", blocked["error"])


if __name__ == "__main__":
    unittest.main()
