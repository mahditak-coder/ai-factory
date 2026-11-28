"""Phase 06 — real local production capabilities, provenance and migration."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import (image_assets, image_providers as ip, production_assets as pa,
                  production_engine as pe, provider_policy as pp,
                  visual_review as vr)

PROJECT = "p06-unit"


def fixture(name=PROJECT):
    d = config.PROJECTS_DIR / name
    (d / "meta.json").parent.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(
        json.dumps({"name": name, "type": "webapp"}), encoding="utf-8")
    return d


class FakeResult:
    def __init__(self, **kw):
        self.__dict__.update({"status": "AVAILABLE", "ok": True, "path": "",
                              "format": "png", "width": 8, "height": 8,
                              "sha256": "", "provider": "fake-free",
                              "model": "fake-model", "cost_note": "free",
                              "error": "", "evidence": {}})
        self.__dict__.update(kw)

    def to_dict(self):
        return dict(self.__dict__)


class FakeProvider:
    id = "fake-free"
    model = "fake-model"
    modality = "image"

    def __init__(self, result=None, status="AVAILABLE"):
        self.calls = 0
        self.result = result
        self._status = status

    def status(self):
        return {"provider": self.id, "model": self.model,
                "status": self._status, "limits": ["mock provider"]}

    def generate(self, prompt, **kwargs):
        self.calls += 1
        if self.result is not None:
            return self.result
        return FakeResult(prompt=prompt, evidence={"kwargs": kwargs})


class ProductionEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name) / "assets.json"
        self.migs = Path(self.tmp.name) / "migrations.json"
        p1 = mock.patch.object(pa, "_STORE", self.store)
        p2 = mock.patch.object(pa, "_MIGRATIONS", self.migs)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)
        self.addCleanup(self.tmp.cleanup)
        shutil.rmtree(config.PROJECTS_DIR / PROJECT, ignore_errors=True)
        self.project_dir = fixture()
        self.calls = []

    def _png(self, rel="assets/generated/image.png"):
        from PIL import Image
        p = self.project_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (16, 12), (10, 20, 30, 255)).save(p, format="PNG")
        return p

    def _wav(self, rel="assets/audio/ambient.wav"):
        spec = __import__("core.tool_registry", fromlist=["registry"])
        result = spec.registry().get("media.generate_wav").adapter(
            PROJECT, {"path": rel, "freq_hz": 440, "duration_ms": 200})
        self.assertTrue(result["size"] > 0)
        return self.project_dir / rel

    def _free_cfg(self):
        return {"providers": [
            {"id": "fake-free", "enabled": True, "paid": False,
             "free_cost_model": True,
             "models": {"fake-model": {"estimated_cost_usd": 0.0}}}]}

    # ------------------------------------------------------------------
    def test_01_real_capability_detection_without_assumption(self):
        cap = pe.capabilities()
        self.assertEqual(cap["arch"], "aarch64")
        self.assertIn("AVAILABLE", cap["tool_status_model"])
        self.assertIn("BLOCKED", cap["tool_status_model"])
        self.assertEqual(cap["media"]["ffprobe"]["status"], "AVAILABLE")
        self.assertEqual(cap["media"]["pillow"]["status"], "AVAILABLE")
        self.assertIn("limits", cap["media"]["chromium_screenshot"])
        self.assertTrue(any("nothing is installed" in n
                            for n in cap["notes"]))

    def test_02_explicit_provider_and_model_required(self):
        for provider, model in (("", "fake-model"), ("auto", "fake-model"),
                                ("fake-free", ""), ("fake-free", "auto")):
            with self.subTest(provider=provider, model=model):
                d = pp.decide(PROJECT, provider, model)
                self.assertFalse(d["ok"])
                self.assertIn("explicit-provider-and-model-required",
                              d["reason"])

    def test_03_free_local_selection_without_payment_approval(self):
        est = {"ok": True, "amount_usd": 0.0, "free": True,
               "source": "test-config"}
        with mock.patch.object(pp, "_cfg", return_value=self._free_cfg()), \
             mock.patch.object(pp.gov, "estimate", return_value=est), \
             mock.patch.object(ip, "get", return_value=FakeProvider()):
            d = pp.decide(PROJECT, "fake-free", "fake-model")
        self.assertTrue(d["ok"], d)
        self.assertTrue(d["free"])
        self.assertEqual(d["estimated_cost_usd"], 0.0)
        self.assertEqual(d["approval_id"], None)
        self.assertEqual(d["credential_source"], "none")

    def test_04_paid_requires_exact_cost_and_explicit_approval(self):
        cfg = {"providers": [{"id": "paid-x", "enabled": True, "paid": True,
                              "credential_env": "P06_TEST_KEY",
                              "models": {"paid-model": {}}}]}
        est = {"ok": True, "amount_usd": 0.25, "free": False,
               "source": "operator-configured"}
        bound = pp.gov.request_hash(pp._payload(
            PROJECT, "paid-x", "paid-model", "standard", "generate",
            "", "", [], ""))
        info = {"ok": True, "expired": False, "project": PROJECT,
                "provider": "paid-x", "model": "paid-model",
                "estimated_cost_usd": 0.25, "request_hash": bound}
        with mock.patch.object(pp, "_cfg", return_value=cfg), \
             mock.patch.object(pp.gov, "estimate", return_value=est), \
             mock.patch.object(pp.gov, "approval_info", return_value=info), \
             mock.patch.object(ip, "get", return_value=FakeProvider()), \
             mock.patch.dict(os.environ, {"P06_TEST_KEY": "SUPERSECRET-VALUE"}):
            no_approval = pp.decide(PROJECT, "paid-x", "paid-model")
            wrong = pp.decide(PROJECT, "paid-x", "paid-model",
                              approval_id="ap-1", confirmed_cost_usd="0.99")
            ok = pp.decide(PROJECT, "paid-x", "paid-model",
                           approval_id="ap-1", confirmed_cost_usd="0.25")
        self.assertIn("paid-model-requires-explicit-approval",
                      no_approval["reason"])
        self.assertIn("paid-cost-mismatch", wrong["reason"])
        self.assertTrue(ok["ok"], ok)
        self.assertEqual(ok["approval_id"], "ap-1")
        self.assertEqual(ok["credential_source"], "environment:P06_TEST_KEY")
        self.assertNotIn("SUPERSECRET-VALUE", json.dumps(ok))

    def test_05_single_call_no_provider_switch_or_fallback(self):
        fake = FakeProvider()
        est = {"ok": True, "amount_usd": 0.0, "free": True,
               "source": "test-config"}
        with mock.patch.object(pp, "_cfg", return_value=self._free_cfg()), \
             mock.patch.object(pp.gov, "estimate", return_value=est), \
             mock.patch.object(ip, "get", return_value=fake):
            d = pp.decide(PROJECT, "fake-free", "fake-model",
                          prompt="a prompt", size="1:1")
            out = pp.dispatch(d, "a prompt", size="1:1")
        self.assertTrue(out["ok"], out)
        self.assertEqual(fake.calls, 1)
        self.assertEqual(out["policy"]["calls"], 1)
        self.assertFalse(out["policy"]["fallback"])
        self.assertIn("no retry", " ".join(d["limits"]))

    def test_06_real_png_validation_and_fake_rejection(self):
        p = self._png()
        info = pa.validate_audio if False else image_assets.validate_png(
            p.read_bytes())
        self.assertEqual([info["width"], info["height"]], [16, 12])
        with self.assertRaises(ValueError):
            image_assets.validate_png(b"this-is-not-a-png")

    def test_07_real_audio_validation_via_detected_tool(self):
        p = self._wav()
        info = pa.validate_audio(p)
        self.assertEqual(info["status"], "AVAILABLE")
        self.assertTrue(info["sha256"])
        self.assertIn(info["validator"], ("ffprobe", "python-wave"))
        fake = self.project_dir / "assets/audio/fake.wav"
        fake.write_text("definitely not audio", encoding="utf-8")
        with self.assertRaises(ValueError):
            pa.validate_audio(fake)

    def test_08_synthesised_audio_is_real_but_non_production(self):
        r = pe.produce_audio(PROJECT, duration_ms=200)
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["production_ready"])
        self.assertEqual(r["classification"],
                         "non-production-synthesised-tone")
        asset = r["asset"]
        self.assertTrue(asset["sha256"])
        self.assertEqual(asset["provenance"]["tool"], "stdlib-wave")
        self.assertEqual(asset["cost_usd"], 0.0)
        self.assertTrue(any("NOT VERIFIED" in x for x in asset["limits"]))
        self.assertTrue(pa.asset_report(PROJECT)["count"] >= 1)

    def test_09_svg_placeholder_never_becomes_production(self):
        p = self.project_dir / "spec/character-sheet.svg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>",
                     encoding="utf-8")
        rec = pa.record(PROJECT, "spec/character-sheet.svg",
                        {"tool": "placeholder"}, production_ready=True)
        self.assertFalse(rec["production_ready"])
        self.assertEqual(rec["classification"],
                         "non-production-vector-placeholder")

    def test_10_migration_is_versioned_idempotent_and_reversible(self):
        svg = self.project_dir / "spec/level-map.svg"
        svg.parent.mkdir(parents=True, exist_ok=True)
        svg.write_text("<svg/>", encoding="utf-8")
        wav = self._wav()
        png = self._png("assets/generated/old.png")
        before = {p: pa.sha256_file(p) for p in (svg, wav, png)}
        first = pa.migrate(PROJECT, "p06-tester")
        self.assertTrue(first["ok"])
        self.assertEqual(first["changed"], 3)
        second = pa.migrate(PROJECT)
        self.assertEqual(second["changed"], 0)  # idempotent
        for p, sha in before.items():
            self.assertEqual(pa.sha256_file(p), sha)  # originals preserved
            sidecar = Path(str(p) + ".production.json")
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertFalse(data["production_ready"])
        mid = first["migration_id"]
        back = pa.rollback(PROJECT, mid)
        self.assertTrue(back["ok"])
        self.assertEqual(back["removed_sidecars"], 3)
        for p in before:
            self.assertFalse(Path(str(p) + ".production.json").exists())
            self.assertEqual(pa.sha256_file(p), before[p])
        self.assertEqual(pa.migration_report(PROJECT)["migrations"], [])

    def test_11_containment_network_blocked(self):
        from core import paths
        for rel in ("../escape.png", "/etc/passwd", "a/../../escape.png"):
            with self.subTest(rel=rel), self.assertRaises(PermissionError):
                paths.project_path(PROJECT, rel)
        blocked = vr.capture(PROJECT, "https://example.com/x.png")
        self.assertFalse(blocked["ok"])
        self.assertTrue("network-isolation-unavailable" in blocked["reason"]
                        or "target" in blocked["reason"])

    def test_12_screenshot_is_evidence_only(self):
        browser = {"status": "AVAILABLE", "tool": "chromium", "path": "/fake",
                   "limits": ["mock"]}
        iso = {"status": "AVAILABLE", "mechanism": "unshare -n",
               "path": "/fake-unshare", "limits": ["mock"]}
        def fake_run(cmd, **kw):
            self.assertEqual(cmd[:2], ["/fake-unshare", "-n"])
            out = cmd[-2].split("=", 1)[1]
            Path(out).write_bytes(self._png("evidence/shot.png").read_bytes())
            self.assertTrue(all("SECRET" not in str(v) for v in
                                kw.get("env", {}).values()))
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(vr, "browser_capability", return_value=browser), \
             mock.patch.object(vr, "network_isolation_capability",
                               return_value=iso), \
             mock.patch.object(vr.subprocess, "run", side_effect=fake_run):
            r = pe.capture_evidence(PROJECT, "about:blank",
                                    "evidence/shot.png")
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["production_ready"])
        self.assertEqual(r["classification"], "evidence-only")
        self.assertTrue(r["asset"]["sha256"])

    def test_13_build_requires_detected_support_and_honest_evidence(self):
        class Adapter:
            def __init__(self, supported):
                self.supported_flag = supported
                self.built = False
            def supported(self, project):
                return {"ok": self.supported_flag}
            def build(self, project, opts):
                self.built = True
                return mock.Mock(ok=False, status="BLOCKED", artifacts={},
                                 error="no real SDK", evidence={})
        a = Adapter(False)
        with mock.patch.object(pe.engine_adapter, "adapter_for",
                               return_value=a):
            r = pe.build(PROJECT, "android")
        self.assertFalse(r["ok"])
        self.assertFalse(a.built)
        self.assertIn("engine-not-supported", r["reason"])

    def test_14_produce_image_records_provenance_and_hash(self):
        p = self._png("assets/generated/from-provider.png")
        result = FakeResult(path="assets/generated/from-provider.png",
                            sha256=image_assets.validate_png(
                                p.read_bytes())["sha256"])
        fake = FakeProvider(result)
        est = {"ok": True, "amount_usd": 0.0, "free": True,
               "source": "test-config"}
        with mock.patch.object(pp, "_cfg", return_value=self._free_cfg()), \
             mock.patch.object(pp.gov, "estimate", return_value=est), \
             mock.patch.object(ip, "get", return_value=fake):
            r = pe.produce_image(PROJECT, "prompt", "fake-free", "fake-model",
                                 rel_path="assets/generated/from-provider.png")
        self.assertTrue(r["ok"], r)
        self.assertEqual(fake.calls, 1)
        self.assertFalse(r["production_ready"])
        asset = r["asset"]
        self.assertEqual(asset["provenance"]["tool"], "fake-free")
        self.assertEqual(asset["provenance"]["model"], "fake-model")
        self.assertTrue(asset["sha256"])
        self.assertEqual(asset["cost_usd"], 0.0)
        self.assertTrue(asset["limits"])

    def test_15_corrupt_store_fails_closed_without_overwrite(self):
        self.store.write_text("{not-json", encoding="utf-8")
        with self.assertRaises(pa.AssetStoreError):
            pa.asset_report(PROJECT)
        p = self._png()
        with self.assertRaises(pa.AssetStoreError):
            pa.record(PROJECT, "assets/generated/image.png", {"tool": "x"})
        self.assertEqual(self.store.read_text(encoding="utf-8"), "{not-json")


if __name__ == "__main__":
    unittest.main()
