"""Independent image provider + REAL local PNG/Sprite processing tests."""
import json, tempfile, unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import image_providers as ip, tool_executor, tool_registry


def fixture(name="img-unit"):
    d = config.PROJECTS_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text('{"name":"%s","type":"webapp"}' % name,
                                 encoding="utf-8")
    return d


class ProviderTest(unittest.TestCase):
    def test_three_independent_providers_and_honest_status(self):
        ids = {p.id for p in ip.PROVIDERS}
        self.assertEqual(ids, {"harness-opencode-image", "opencode-image",
                               "local-pillow"})
        by = {p.id: p.status() for p in ip.PROVIDERS}
        self.assertEqual(by["harness-opencode-image"]["status"], ip.BLOCKED)
        self.assertEqual(by["opencode-image"]["status"], ip.BLOCKED)
        self.assertEqual(by["local-pillow"]["status"], ip.AVAILABLE)
        self.assertIn("credential", by["harness-opencode-image"]["reason"].lower())
        self.assertTrue(all(p.modality == "image" for p in ip.PROVIDERS))

    def test_independent_from_text_provider_and_no_secrets(self):
        # Core runner may point config.PROJECT_ROOT at a temp root; read the
        # repository config from the test file's location instead.
        repo = Path(__file__).resolve().parents[2]
        cfg = json.loads((repo / "config" / "image_providers.json").read_text())
        self.assertEqual(cfg.get("active"), "")
        self.assertFalse(ip.active())
        self.assertTrue(all(str(v.get("credential", "")).lower() in
                            ("not configured", "not required", "")
                            for v in cfg["providers"]))
        for v in cfg["providers"]:
            env = str(v.get("credential_env") or "")
            self.assertFalse(env.startswith("sk-"))
            self.assertLessEqual(len(env), 40)
        self.assertFalse(ip.record_probe("Invalid credential")["secrets_exposed"])
        # Static independence check — never spawn OpenCode inside a unit test.
        from core.model_gateway import ModelGateway
        text_api = {n for n in dir(ModelGateway) if not n.startswith("_")}
        self.assertFalse(any("image" in n for n in text_api))
        # The image model id may be present; a credential/secret never is.
        self.assertNotIn("credential_value", json.dumps(cfg))
        self.assertTrue(all(str(v.get("credential", "")).lower()
                            in ("not configured", "not required", "")
                            for v in cfg["providers"]))

    def test_blocked_generation_never_creates_a_file(self):
        d = fixture()
        before = set(p.name for p in d.rglob("*") if p.is_file())
        r = ip.get("harness-opencode-image").generate("fox")
        self.assertEqual(r.status, ip.BLOCKED)
        self.assertFalse(r.ok)
        self.assertFalse(r.path)
        self.assertIn("credential", r.error.lower())
        after = set(p.name for p in d.rglob("*") if p.is_file())
        self.assertEqual(before, after)

    def test_tools_registered_with_independent_scopes(self):
        ids = {s.id for s in tool_registry.registry().list()}
        self.assertIn("image.generate", ids)
        self.assertIn("image.process", ids)
        by = {s.id: s for s in tool_registry.registry().list()}
        self.assertEqual(by["image.generate"].scope, "image:generate")
        self.assertEqual(by["image.process"].scope, "image:write")


class ProcessingTest(unittest.TestCase):
    def setUp(self):
        from PIL import Image, ImageDraw
        self.d = fixture("img-process")
        im = Image.new("RGBA", (128, 128), (255, 255, 255, 255))
        dr = ImageDraw.Draw(im)
        dr.ellipse((24, 24, 104, 104), fill=(210, 120, 40, 255))
        im.save(self.d / "source.png")

    def test_inspect_is_real_measurement(self):
        r = ip.process("img-process", {"op": "inspect", "path": "source.png"})
        self.assertTrue(r.ok)
        self.assertEqual((r.width, r.height), (128, 128))
        self.assertTrue(r.evidence["sha256"])
        self.assertEqual(len(r.sha256), 64)
        self.assertFalse(r.evidence["has_alpha"])

    def test_white_to_alpha_creates_real_transparent_png(self):
        r = ip.process("img-process", {"op": "white_to_alpha",
            "path": "source.png", "output": "fox_cutout.png", "threshold": 245})
        self.assertTrue(r.ok, r.error)
        out = self.d / "fox_cutout.png"
        self.assertTrue(out.is_file())
        from PIL import Image
        with Image.open(out) as im:
            a = im.getchannel("A")
            self.assertLess(a.getextrema()[0], 255)
            self.assertEqual(a.getextrema()[1], 255)
            self.assertEqual(im.size, (128, 128))
        self.assertGreater(r.evidence["transparent_pixels"], 5000)
        self.assertEqual(len(r.sha256), 64)

    def test_resize_and_sprite_sheet_preparation(self):
        r = ip.process("img-process", {"op": "resize", "path": "source.png",
            "output": "fox_64.png", "width": 64, "height": 64})
        self.assertTrue(r.ok)
        self.assertEqual((r.width, r.height), (64, 64))
        check = ip.process("img-process", {"op": "sprite_sheet",
            "path": "fox_64.png", "frame_width": 32, "frame_height": 32})
        self.assertTrue(check.ok)
        self.assertEqual(check.evidence["frames"], 4)
        sheet = ip.process("img-process", {"op": "sprite_sheet",
            "path": "fox_64.png", "frame_width": 32, "frame_height": 32,
            "output": "fox_sheet.png"})
        self.assertTrue(sheet.ok)
        from PIL import Image
        with Image.open(self.d / "fox_sheet.png") as im:
            self.assertEqual(im.size, (64, 64))

    def test_governed_tool_execution_and_scope_denial(self):
        r = tool_executor.execute(tool_executor.ToolRequest(
            tool_id="image.process", project="img-process",
            inputs={"op": "inspect", "path": "source.png"},
            scopes=["image:write"], task_id="img-test", call_id="img-inspect-1"))
        self.assertEqual(r.status, "PASS", r.error)
        denied = tool_executor.execute(tool_executor.ToolRequest(
            tool_id="image.process", project="img-process",
            inputs={"op": "inspect", "path": "source.png"},
            scopes=[], task_id="img-test", call_id="img-inspect-2"))
        self.assertEqual(denied.status, "BLOCKED")
        self.assertEqual(denied.error, "scope-not-granted")


if __name__ == "__main__":
    unittest.main()
