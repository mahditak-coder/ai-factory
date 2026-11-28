"""Mocked proof that image.generate passes prompt exactly once.

No real provider request, no approval creation/consumption.
"""
import base64, io, os, tempfile, unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import core.config as config
from core import image_governance as gov, image_providers as ip, image_remote as ir
from core import paths, tool_events, tool_executor
from core.tests import trusted_request

MODEL = "recraft/recraft-v4.1-flash-v2:free"
BUF = io.BytesIO(); Image.new("RGBA", (32, 32), (200, 100, 50, 255)).save(BUF, "PNG")
PNG = BUF.getvalue()
B64 = base64.b64encode(PNG).decode()


def request_inputs(prompt="one prompt", output="assets/generated/adapter-test.png"):
    return {"provider": "openrouter-image", "model": MODEL, "prompt": prompt,
            "operation": "generate", "aspect_ratio": "1:1", "n": 1,
            "output": output, "approval_id": "test-only-not-real",
            "confirmed_cost_usd": "0"}


class AdapterTest(unittest.TestCase):
    def setUp(self):
        d = config.PROJECTS_DIR / "img-adapter-unit"
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text('{"name":"img-adapter-unit","type":"webapp"}')
        tf = tempfile.NamedTemporaryFile(suffix=".json")
        Path(tf.name).write_text("[]", encoding="utf-8")
        self.p = mock.patch.object(tool_events, "_STORE", Path(tf.name))
        self.p.start(); self._tf = tf

    def tearDown(self):
        self.p.stop(); self._tf.close()
        import shutil
        shutil.rmtree(config.PROJECTS_DIR / "img-adapter-unit", ignore_errors=True)

    def call(self, inputs):
        return tool_executor.execute(trusted_request(
            tool_id="image.generate", project="img-adapter-unit", inputs=inputs,
            scopes=["image:generate"], task_id="adapter-test",
            call_id="adapter-" + os.urandom(6).hex()))

    def test_prompt_reaches_provider_exactly_once_with_other_fields_intact(self):
        captured = {}
        class Fake:
            def generate(self, prompt, **kw):
                captured["prompt"] = prompt
                captured["kw"] = kw
                return ip.ImageResult(status="PASS", ok=True, provider="fake",
                                      evidence={"seen": prompt})
        with mock.patch.object(ip, "get", lambda pid: Fake()):
            r = self.call(request_inputs(prompt="EXACT PROMPT"))
        self.assertEqual(r.status, "PASS", r.error)
        self.assertEqual(captured["prompt"], "EXACT PROMPT")
        self.assertNotIn("prompt", captured["kw"])
        self.assertEqual(captured["kw"]["model"], MODEL)
        self.assertEqual(captured["kw"]["approval_id"], "test-only-not-real")
        self.assertEqual(captured["kw"]["aspect_ratio"], "1:1")
        self.assertEqual(captured["kw"]["n"], 1)
        self.assertEqual(captured["kw"]["output"], "assets/generated/adapter-test.png")

    def test_edit_provider_still_receives_prompt_in_kwargs(self):
        captured = {}
        class Fake:
            def edit(self, req):
                captured["req"] = req
                return ip.ImageResult(status="PASS", ok=True, provider="fake")
        with mock.patch.object(ip, "get", lambda pid: Fake()):
            r = self.call({**request_inputs(), "operation": "edit"})
        self.assertEqual(r.status, "PASS", r.error)
        self.assertEqual(captured["req"]["prompt"], "one prompt")

    def test_success_and_provider_error_through_governed_tool(self):
        def ok_http(method, url, headers, body, timeout):
            return 200, {}, json_response({"data": [{"b64_json": B64}],
                                           "usage": {"cost": 0}})
        def json_response(obj):
            import json
            return json.dumps(obj).encode()
        def bad_http(method, url, headers, body, timeout):
            return 401, {}, b'{"error":{"message":"invalid key"}}'
        repo_cfg = Path(__file__).resolve().parents[2] / "config" / "image_providers.json"
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-only"}), \
             mock.patch.object(gov, "_CONFIG", repo_cfg), \
             mock.patch.object(gov, "consume", return_value={
                 "status": "PASS", "ok": True, "cost_usd": 0.0}), \
             mock.patch.object(gov, "record_usage", return_value={}), \
             mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("REAL NETWORK")):
            with mock.patch.object(ir, "_http", ok_http):
                good = self.call(request_inputs(output="assets/generated/adapter-ok.png"))
                self.assertEqual(good.status, "PASS", good.error)
                out = good.output or {}
                self.assertEqual(out.get("status"), "PASS")
                self.assertEqual(out.get("model"), MODEL)
                self.assertEqual(out.get("sha256"), out.get("evidence", {}).get(
                    "asset", {}).get("sha256"))
                self.assertTrue((paths.project_dir("img-adapter-unit") /
                                 out["path"]).is_file())
            with mock.patch.object(ir, "_http", bad_http):
                bad = self.call(request_inputs(output="assets/generated/adapter-bad.png"))
                self.assertEqual(bad.status, "PASS", bad.error)  # tool executed
                self.assertEqual((bad.output or {}).get("status"), "FAIL")
                self.assertIn("401", (bad.output or {}).get("error", ""))


if __name__ == "__main__":
    unittest.main()
