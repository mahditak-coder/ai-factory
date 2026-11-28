"""Phase 06 final acceptance gate — MOCKED transport, no real provider call."""
import base64
import io
import json
import multiprocessing
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import core.config as config
from core import (image_governance as gov, image_providers as ip,
                  image_remote as ir, production_assets as pa,
                  production_engine as pe, provider_policy as pp,
                  visual_review as vr)

PROJECT = "p06-gate"
FREE = "recraft/recraft-v4.1-flash-v2:free"
PAID = "openrouter/example-paid-model"
BUF = io.BytesIO()
Image.new("RGBA", (32, 32), (40, 80, 120, 255)).save(BUF, "PNG")
PNG = BUF.getvalue()
B64 = base64.b64encode(PNG).decode()


def transport(model, cost, fail=False):
    calls = []

    def handler(method, url, headers, body, timeout):
        calls.append(body)
        if fail:
            raise RuntimeError("mock-transport-failure")
        payload = {"model": model,
                   "data": [{"b64_json": B64, "media_type": "image/png"}],
                   "usage": {"cost": cost}}
        return 200, {}, json.dumps(payload).encode()
    return handler, calls


def _worker(decision, cfg_path, store_path, queue):
    try:
        pp._CONFIG = Path(cfg_path)
        ip._CONFIG = Path(cfg_path)
        gov._CONFIG = Path(cfg_path)
        gov._STORE = Path(store_path)
        os.environ["OPENROUTER_API_KEY"] = "test-only"
        queue.put(("ok", pp.consume_approval(decision)))
    except Exception as exc:  # noqa: BLE001
        queue.put(("error", repr(exc)))


class AcceptanceGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.cfg = root / "image_providers.json"
        self.store = root / "image_usage.json"
        self.cfg.write_text(json.dumps({
            "spending": {"per_request_usd": 2.0, "daily_usd": 10.0},
            "providers": [{
                "id": "openrouter-image", "enabled": True, "paid": False,
                "free_cost_model": True, "credential_env": "OPENROUTER_API_KEY",
                "models": {
                    FREE: {"estimated_cost_usd": 0.0, "free": True},
                    PAID: {"estimated_cost_usd": 0.25, "paid": True}}},
                {"id": "local-pillow", "enabled": True, "paid": False,
                 "credential": "not required",
                 "capabilities": ["inspect", "resize"]}]}),
            encoding="utf-8")
        self.store.write_text("{}", encoding="utf-8")
        d = config.PROJECTS_DIR / PROJECT
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(
            json.dumps({"name": PROJECT, "type": "webapp"}), encoding="utf-8")
        for patch in (
            mock.patch.object(pp, "_CONFIG", self.cfg),
            mock.patch.object(ip, "_CONFIG", self.cfg),
            mock.patch.object(gov, "_CONFIG", self.cfg),
            mock.patch.object(gov, "_STORE", self.store),
            mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-only"}),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _free(self, prompt="free prompt"):
        return pp.decide(PROJECT, "openrouter-image", FREE, prompt=prompt,
                         size="512x512")

    def _paid(self, prompt="paid prompt"):
        payload = {"provider": "openrouter-image", "model": PAID,
                   "quality": "standard", "prompt": prompt, "size": "512x512",
                   "references": [], "operation": "generate",
                   "project": PROJECT}
        a = gov.request_approval(PROJECT, gov.payload_from(PROJECT, payload),
                                 "tester")
        self.assertEqual(gov.decide(a["approval_id"], "approve",
                                    "tester")["status"], "PASS")
        return pp.decide(PROJECT, "openrouter-image", PAID, prompt=prompt,
                         size="512x512", approval_id=a["approval_id"],
                         confirmed_cost_usd="0.25"), a["approval_id"]

    # ------------------------------------------------- required cases 1-8
    def test_01_free_decision_success_exactly_once(self):
        d = self._free()
        gate1 = pp.consume_approval(d)
        gate2 = pp.consume_approval(d)
        self.assertTrue(gate1["ok"], gate1)
        self.assertFalse(gate2["ok"])
        self.assertEqual(gate2["reason"], "decision-already-consumed")

    def test_02_second_free_dispatch_rejected(self):
        d = self._free()
        handler, calls = transport(FREE, 0.0)
        with mock.patch.object(ir, "_http", handler), \
             mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("REAL NETWORK")):
            first = pp.dispatch(d, "free prompt", size="512x512")
            second = pp.dispatch(d, "free prompt", size="512x512")
        self.assertTrue(first["ok"], first)
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason"], "decision-already-consumed")
        self.assertEqual(len(calls), 1)

    def test_03_concurrent_processes_free_decision_one_winner(self):
        d = self._free("race")
        queue = multiprocessing.Queue()
        procs = [multiprocessing.Process(
            target=_worker, args=(d, str(self.cfg), str(self.store), queue))
            for _ in range(4)]
        for p in procs:
            p.start()
        results = [queue.get(timeout=40) for _ in procs]
        for p in procs:
            p.join(timeout=40)
        self.assertEqual(len(results), 4)
        self.assertEqual([kind for kind, _ in results].count("ok"), 4,
                         str(results))
        wins = sum(1 for _, gate in results
                   if isinstance(gate, dict) and gate.get("ok"))
        self.assertEqual(wins, 1, results)
        losses = [g for _, g in results if not g.get("ok")]
        self.assertTrue(all(g["reason"] == "decision-already-consumed"
                            for g in losses))

    def test_04_replay_after_provider_failure_rejected(self):
        d = self._free()
        failing, calls = transport(FREE, 0.0, fail=True)
        with mock.patch.object(ir, "_http", failing):
            first = pp.dispatch(d, "free prompt", size="512x512")
            second = pp.dispatch(d, "free prompt", size="512x512")
        self.assertFalse(first["ok"])
        self.assertIn("transport-error", str(first))
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason"], "decision-already-consumed")
        self.assertEqual(len(calls), 1)   # no second external attempt

    def test_05_paid_still_consumes_exactly_once(self):
        d, aid = self._paid()
        handler, calls = transport(PAID, 0.25)
        with mock.patch.object(ir, "_http", handler), \
             mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("REAL NETWORK")):
            out = pp.dispatch(d, "paid prompt", size="512x512")
        self.assertTrue(out["ok"], out)
        self.assertEqual(len(calls), 1)
        self.assertTrue(gov.approval_info(aid).get("consumed"))
        self.assertFalse(pp.consume_approval(d)["ok"])

    def test_06_forged_and_mutated_decisions_rejected(self):
        forged = {"ok": True, "status": "PASS", "free": True,
                  "provider": "openrouter-image", "model": PAID,
                  "estimated_cost_usd": 0.0, "approval_id": "",
                  "request_hash": "x", "project": PROJECT}
        handler, calls = transport(PAID, 0.25)
        with mock.patch.object(ir, "_http", handler):
            for bad in (forged, dict(forged, confirmed_cost_usd="100")):
                self.assertFalse(pp.dispatch(bad, "x")["ok"])
        self.assertEqual(calls, [])
        real = self._free()
        for change in ({"free": False}, {"model": PAID},
                       {"request_hash": "forged"}, {"prompt": "changed"}):
            self.assertFalse(pp.consume_approval(dict(real, **change))["ok"])

    def test_07_no_transport_for_rejected_paths(self):
        handler, calls = transport(FREE, 0.0)
        d = self._free()
        forged = dict(d, model=PAID)
        with mock.patch.object(ir, "_http", handler):
            pp.dispatch(d, "wrong prompt")
            pp.dispatch(forged, "free prompt")
            pp.dispatch({"decision_id": "bogus"}, "free prompt")
        self.assertEqual(calls, [])

    def test_08_free_cost_cap_cannot_be_overridden_by_caller(self):
        d = self._free()
        handler, calls = transport(FREE, 0.04)  # unexpected nonzero cost
        with mock.patch.object(ir, "_http", handler):
            out = pp.dispatch(d, "free prompt", size="512x512",
                              confirmed_cost_usd="100")
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "caller-authorization-kwargs-rejected")
        self.assertEqual(calls, [])

    # ------------------------------------------------- other fixed contracts
    def test_09_expired_approval_rejected_by_governance_directly(self):
        payload = {"provider": "openrouter-image", "model": PAID,
                   "prompt": "old", "operation": "generate",
                   "project": PROJECT}
        a = gov.request_approval(PROJECT, gov.payload_from(PROJECT, payload),
                                 "tester")
        gov.decide(a["approval_id"], "approve", "tester")
        data = json.loads(self.store.read_text(encoding="utf-8"))
        data["approvals"][a["approval_id"]]["at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 3600))
        self.store.write_text(json.dumps(data), encoding="utf-8")
        verdict = gov.consume(a["approval_id"],
                              gov.payload_from(PROJECT, payload))
        self.assertFalse(verdict.get("ok"))
        self.assertIn("expired", str(verdict.get("error")))

    def test_10_build_modes_fail_closed_not_crash(self):
        for mode in ("web", "backend", "ai-agent", "game-2d"):
            with self.subTest(mode=mode):
                out = pe.build(PROJECT, mode)
                self.assertFalse(out["ok"])
                self.assertIn(out["status"], ("BLOCKED", "FAIL", "NOT VERIFIED"))
                self.assertIn("reason", out)
        self.assertEqual(pe.build(PROJECT, "unknown")["status"], "BLOCKED")

    def test_11_fake_images_and_audio_rejected(self):
        fake = self.project_dir() / "bad.jpg"
        fake.write_text("not an image", encoding="utf-8")
        rec = pa.record(PROJECT, "bad.jpg", {"tool": "x"},
                        production_ready=True)
        self.assertFalse(rec["production_ready"])
        self.assertFalse(rec["validation"]["validated"])
        wav = self.project_dir() / "fake.wav"
        wav.write_bytes(BUF.getvalue())   # PNG bytes named .wav
        with self.assertRaises(ValueError):
            pa.validate_audio(wav)

    def test_12_missing_provider_model_in_response_rejected(self):
        d = self._free()
        def handler(method, url, headers, body, timeout):
            payload = {"data": [{"b64_json": B64}],
                       "usage": {"cost": 0}}
            return 200, {}, json.dumps(payload).encode()
        with mock.patch.object(ir, "_http", handler):
            out = pp.dispatch(d, "free prompt", size="512x512")
        self.assertFalse(out["ok"])
        self.assertIn("returned-model-mismatch", out.get("error", ""))

    def test_13_non_generate_operations_are_local_only(self):
        out = pp.decide(PROJECT, "openrouter-image", FREE,
                        operation="resize", prompt="x")
        self.assertFalse(out["ok"])
        self.assertIn("non-generate-operations-are-local-only", out["reason"])

    def project_dir(self):
        return config.PROJECTS_DIR / PROJECT


if __name__ == "__main__":
    unittest.main()
