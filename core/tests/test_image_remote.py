"""Mocked official-provider tests — NO real network, NO paid request."""
import base64, io, json, os, tempfile, unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import core.config as config
from core import (image_assets, image_governance as gov, image_providers as ip,
                  image_remote as ir, paths, tool_executor)


def png_bytes(color=(180, 90, 40, 255)):
    im = Image.new("RGBA", (64, 64), color)
    buf = io.BytesIO(); im.save(buf, format="PNG")
    return buf.getvalue()


PNG = png_bytes()
B64 = base64.b64encode(PNG).decode()


class Base(unittest.TestCase):
    def setUp(self):
        fixture = config.PROJECTS_DIR / "img-remote-unit"
        fixture.mkdir(parents=True, exist_ok=True)
        (fixture / "meta.json").write_text('{"name":"img-remote-unit","type":"webapp"}')
        self.tf = tempfile.NamedTemporaryFile(suffix=".json")
        Path(self.tf.name).write_text("{}", encoding="utf-8")
        self.cfgf = tempfile.NamedTemporaryFile(suffix=".json")
        cfg = {"spending": {"per_request_usd": 1.0, "daily_usd": 5.0},
               "providers": []}
        for pid, models in (("openai-image", ir.OPENAI_MODELS),
                            ("gemini-image", ir.GEMINI_MODELS),
                            ("bfl-image", ir.BFL_MODELS)):
            cfg["providers"].append({"id": pid,
                "models": {m: {"estimated_cost_usd": 0.1} for m in models}})
        Path(self.cfgf.name).write_text(json.dumps(cfg), encoding="utf-8")
        self.pu = mock.patch.object(gov, "_STORE", Path(self.tf.name))
        self.pc = mock.patch.object(gov, "_CONFIG", Path(self.cfgf.name))
        self.pu.start(); self.pc.start()
        self.calls = []

    def tearDown(self):
        self.pu.stop(); self.pc.stop(); self.tf.close(); self.cfgf.close()

    def fake_http(self, responses):
        def f(method, url, headers, body, timeout):
            self.calls.append((method, url, body, headers))
            return responses(method, url, body)
        return f

    def approve(self, project, provider, model, **extra):
        req = {"provider": provider, "model": model, "quality": "standard",
               "prompt": "professional gouache fox", "size": "1024x1024",
               "references": [], "operation": "generate"}
        req.update(extra)
        req["project"] = project
        a = gov.request_approval(project, gov.payload_from(project, req), "tester")
        self.assertEqual(a["status"], "PASS", a)
        self.assertEqual(gov.decide(a["approval_id"], "approve", "tester")["status"], "PASS")
        req["approval_id"] = a["approval_id"]
        req["confirmed_cost_usd"] = "0.1"
        return req

    def openai_http(self, method, url, body):
        return 200, {}, json.dumps({"data": [{"b64_json": B64}],
                                    "usage": {"total_tokens": 12}}).encode()

    def gemini_http(self, method, url, body):
        return 200, {}, json.dumps({"id": "turn-1",
                                    "output_image": {"data": B64}}).encode()

    def bfl_http(self, method, url, body):
        if method == "POST":
            return 200, {}, json.dumps({"id": "req-1",
                "polling_url": "https://api.bfl.ai/v1/get_result?id=req-1"}).encode()
        return 200, {}, json.dumps({"status": "Ready", "result": {
            "sample": "https://delivery.bfl.ai/sample.png"}}).encode()


class ProviderTest(Base):
    def test_no_real_network_and_no_auto_fallback(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}), \
             mock.patch.object(ir, "_http", self.fake_http(self.openai_http)), \
             mock.patch.object(ir, "_download", lambda *a: PNG), \
             mock.patch("urllib.request.urlopen", side_effect=AssertionError("NETWORK")):
            p = ip.get("openai-image")
            r = p.generate("no approval", model="gpt-image-2.6-flare",
                           project="img-remote-unit")
            self.assertEqual(r.status, "BLOCKED")
            self.assertEqual(r.error, "approval-required")
            self.assertEqual(self.calls, [])

    def test_three_official_providers_are_registered_with_env_credentials(self):
        ids = [p.id for p in ip._all()]
        for expected in ("openai-image", "gemini-image", "bfl-image"):
            self.assertIn(expected, ids)
        for pid, env in (("openai-image", "OPENAI_API_KEY"),
                         ("gemini-image", "GEMINI_API_KEY"),
                         ("bfl-image", "BFL_API_KEY")):
            st = ip.get(pid).status()
            self.assertEqual(st["credential_env"], env)
            self.assertFalse(st["authenticated"])
            self.assertFalse(st["active"])
            self.assertIn("credential_presence", st)
            self.assertFalse(st["authenticated"])

    def test_openai_generation_and_edit_with_reference(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}), \
             mock.patch.object(ir, "_http", self.fake_http(self.openai_http)), \
             mock.patch.object(ir, "_download", lambda *a: PNG):
            p = ip.get("openai-image")
            req = self.approve("img-remote-unit", "openai-image",
                               "gpt-image-2.6-sunburst", operation="edit",
                               references=["https://example.test/ref.png"])
            r = p.generate(req.pop("prompt"), **req)
            self.assertEqual(r.status, "PASS", r.error)
            self.assertTrue(r.path.startswith("assets/generated"))
            meta = json.loads((paths.project_dir("img-remote-unit") /
                               (r.path + ".json")).read_text())
            self.assertEqual(meta["provider"], "openai-image")
            self.assertEqual(meta["model"], "gpt-image-2.6-sunburst")
            self.assertTrue(meta["reference_hashes"])
            self.assertTrue(meta["usage"])
            body = json.dumps(self.calls[-1][2])
            self.assertIn("gpt-image-2.6-sunburst", body)
            self.assertIn("images", body)  # edit endpoint used for refs

    def test_gemini_multiturn_and_reference_limit(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-only"}), \
             mock.patch.object(ir, "_http", self.fake_http(self.gemini_http)), \
             mock.patch.object(ir, "_download", lambda *a: PNG):
            p = ip.get("gemini-image")
            req = self.approve("img-remote-unit", "gemini-image",
                               "gemini-nano-banana-2.1",
                               previous_interaction_id="turn-0")
            r = p.generate(req.pop("prompt"), **req)
            self.assertEqual(r.status, "PASS", r.error)
            body = self.calls[-1][2]
            if isinstance(body, (str, bytes)):
                body = json.loads(body)
            self.assertEqual(body["previous_interaction_id"], "turn-0")
            self.assertEqual(body["model"], "gemini-nano-banana-2.1")
            self.assertIn("Api-Revision", self.calls[-1][3])
            req2 = self.approve("img-remote-unit", "gemini-image",
                                "gemini-3-pro-image",
                                references=["https://x/%d.png" % i for i in range(6)])
            r2 = p.generate(req2.pop("prompt"), **req2)
            self.assertEqual(r2.status, "FAIL")
            self.assertIn("reference-count", r2.error)

    def test_bfl_polling_and_local_reference_blocked(self):
        with mock.patch.dict(os.environ, {"BFL_API_KEY": "test-only"}), \
             mock.patch.object(ir, "_http", self.fake_http(self.bfl_http)), \
             mock.patch.object(ir, "_download", lambda *a: PNG):
            p = ip.get("bfl-image")
            req = self.approve("img-remote-unit", "bfl-image", "flux-2-max")
            r = p.generate(req.pop("prompt"), **req)
            self.assertEqual(r.status, "PASS", r.error)
            self.assertTrue(any(c[1].endswith("/flux-2-max") for c in self.calls))
            req2 = self.approve("img-remote-unit", "bfl-image", "flux-2-pro",
                                references=["/etc/passwd"])
            r2 = p.generate(req2.pop("prompt"), **req2)
            self.assertEqual(r2.status, "BLOCKED")
            self.assertIn("reference", r2.error)

    def test_auth_failure_malformed_timeout_and_missing_credential(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "bad"}):
            req = self.approve("img-remote-unit", "openai-image", "gpt-image-2.6-flare")
            with mock.patch.object(ir, "_http", lambda *a: (
                    401, {}, b'{"error":{"message":"invalid key"}}')):
                r = ip.get("openai-image").generate(req.pop("prompt"), **req)
                self.assertEqual(r.status, "FAIL")
                self.assertIn("401", r.error)
            req = self.approve("img-remote-unit", "openai-image", "gpt-image-2.6-flare")
            with mock.patch.object(ir, "_http", lambda *a: (200, {}, b"not-json")):
                r = ip.get("openai-image").generate(req.pop("prompt"), **req)
                self.assertEqual(r.status, "FAIL")
                self.assertIn("malformed-json", r.error)
            req = self.approve("img-remote-unit", "openai-image", "gpt-image-2.6-flare")
            def boom(*a): raise TimeoutError("slow")
            with mock.patch.object(ir, "_http", boom):
                r = ip.get("openai-image").generate(req.pop("prompt"), **req)
                self.assertEqual(r.status, "FAIL")
                self.assertNotEqual(r.status, "PASS")
        with mock.patch.dict(os.environ, {}, clear=True):
            r = ip.get("openai-image").generate("x", approval_id="none",
                                                confirmed_cost_usd="0.1")
            self.assertEqual(r.status, "BLOCKED")
            self.assertIn("missing-credential", r.error)

    def test_cost_unknown_and_limits_reject_before_any_call(self):
        req = {"provider": "openai-image", "model": "gpt-image-2.6-flare",
               "prompt": "x", "operation": "generate"}
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "x"}), \
             mock.patch.object(ir, "_http", side_effect=AssertionError("NETWORK")):
            a = gov.request_approval("img-remote-unit", req, "tester")
            self.assertEqual(a["status"], "PASS")
            blocked = gov.request_approval("img-remote-unit", {**req, "model": "unknown"},
                                           "tester")
            self.assertEqual(blocked["error"], "cost-estimate-unavailable")
            # decision is not approval: a pending approval cannot be consumed
            r = ip.get("openai-image").generate(
                "x", model="gpt-image-2.6-flare", project="img-remote-unit",
                approval_id=a["approval_id"], confirmed_cost_usd="0.1")
            self.assertEqual(r.status, "BLOCKED")
            self.assertIn("approval-not-granted", r.error)

    def test_asset_versions_never_overwrite_and_png_validated(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "x"}), \
             mock.patch.object(ir, "_http", self.fake_http(self.openai_http)):
            req = self.approve("img-remote-unit", "openai-image", "gpt-image-2.6-flare")
            first = ip.get("openai-image").generate(req.pop("prompt"), **req)
            req = self.approve("img-remote-unit", "openai-image", "gpt-image-2.6-flare")
            second = ip.get("openai-image").generate(req.pop("prompt"), **req)
            self.assertEqual(first.status, "PASS")
            self.assertEqual(second.status, "PASS")
            self.assertNotEqual(first.path, second.path)
            self.assertTrue((paths.project_dir("img-remote-unit") /
                             first.path).is_file())
        with self.assertRaises(ValueError):
            image_assets.validate_png(b"not-a-png")

    def test_output_path_restriction(self):
        with self.assertRaises(Exception):
            paths.project_path("img-remote-unit", "../outside.png")


if __name__ == "__main__":
    unittest.main()
