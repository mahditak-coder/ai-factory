"""Phase 06 — approval-decision forgery hardening (MOCKED transport only).

No real provider call, no paid spend. Every transport call in this file is an
injected fake; a real network attempt raises an assertion.
"""
import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import core.config as config
from core import (image_governance as gov, image_providers as ip,
                  image_remote as ir, provider_policy as pp)

PROJECT = "p06-forgery"
FREE = "recraft/recraft-v4.1-flash-v2:free"
PAID = "openrouter/example-paid-model"
BUF = io.BytesIO()
Image.new("RGBA", (24, 24), (30, 60, 90, 255)).save(BUF, "PNG")
B64 = base64.b64encode(BUF.getvalue()).decode()


def fake_transport(model, cost):
    calls = []

    def handler(method, url, headers, body, timeout):
        calls.append(body)
        payload = {"model": model, "data": [{"b64_json": B64,
                                             "media_type": "image/png"}],
                   "usage": {"cost": cost}}
        return 200, {}, json.dumps(payload).encode()
    return handler, calls


class ForgeryHardeningTest(unittest.TestCase):
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
                    PAID: {"estimated_cost_usd": 0.25, "paid": True}}}]}),
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

    def _approval(self, prompt="paid prompt"):
        payload = {"provider": "openrouter-image", "model": PAID,
                   "quality": "standard", "prompt": prompt, "size": "512x512",
                   "references": [], "operation": "generate",
                   "project": PROJECT}
        r = gov.request_approval(PROJECT, gov.payload_from(PROJECT, payload),
                                 "tester")
        self.assertEqual(r["status"], "PASS", r)
        self.assertEqual(gov.decide(r["approval_id"], "approve",
                                    "tester")["status"], "PASS")
        return r["approval_id"]

    def _paid_decision(self, aid=None, prompt="paid prompt"):
        aid = aid or self._approval(prompt)
        return pp.decide(PROJECT, "openrouter-image", PAID, prompt=prompt,
                         size="512x512", approval_id=aid,
                         confirmed_cost_usd="0.25")

    def _free_decision(self):
        return pp.decide(PROJECT, "openrouter-image", FREE,
                         prompt="free prompt", size="512x512")

    # ------------------------------------------------------------- forgeries
    def test_01_hand_forged_free_true_for_paid_model_rejected(self):
        forged = {"ok": True, "status": "PASS", "free": True,
                  "provider": "openrouter-image", "model": PAID,
                  "estimated_cost_usd": 0.0, "request_hash": "forged",
                  "approval_id": "", "project": PROJECT, "prompt": "x",
                  "operation": "generate", "quality": "standard"}
        out = pp.consume_approval(forged)
        self.assertFalse(out["ok"])
        self.assertIn("decision-not-registered", out["reason"])
        transport, calls = fake_transport(PAID, 0.25)
        with mock.patch.object(ir, "_http", transport), \
             mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("REAL NETWORK")):
            dispatched = pp.dispatch(forged, "x")
        self.assertFalse(dispatched["ok"])
        self.assertEqual(calls, [])

    def test_02_mutated_fields_on_real_decision_rejected(self):
        original = self._paid_decision()
        self.assertTrue(original["ok"], original)
        mutations = (
            {"free": True}, {"estimated_cost_usd": 0.0},
            {"confirmed_cost_usd": 0.0}, {"provider": "openai-image"},
            {"model": FREE}, {"request_hash": "forged"},
            {"approval_id": "forged"}, {"prompt": "mutated"},
        )
        for change in mutations:
            with self.subTest(change=change):
                forged = dict(original)
                forged.update(change)
                out = pp.consume_approval(forged)
                self.assertFalse(out["ok"])
                self.assertEqual(out["reason"],
                                 "decision-mutated-after-validation")
        transport, calls = fake_transport(PAID, 0.25)
        with mock.patch.object(ir, "_http", transport):
            self.assertFalse(pp.dispatch(dict(original, free=True),
                                        "paid prompt")["ok"])
        self.assertEqual(calls, [])

    def test_03_forged_free_flag_cannot_downgrade_paid_model(self):
        real = self._paid_decision()
        forged = dict(real)
        forged["free"] = True
        forged["estimated_cost_usd"] = 0.0
        forged["approval_id"] = ""
        out = pp.consume_approval(forged)
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "decision-mutated-after-validation")
        self.assertIsNot(gov.approval_info(real["approval_id"]).get("consumed"),
                         True)

    # ---------------------------------------------------- genuine operations
    def test_04_legitimate_free_model_without_paid_approval(self):
        d = self._free_decision()
        transport, calls = fake_transport(FREE, 0.0)
        with mock.patch.object(ir, "_http", transport), \
             mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("REAL NETWORK")):
            out = pp.dispatch(d, "free prompt", size="512x512")
        self.assertTrue(out["ok"], out)
        self.assertEqual(len(calls), 1)
        body = calls[0]
        body = json.loads(body) if isinstance(body, (str, bytes)) else body
        self.assertEqual(body["model"], FREE)
        self.assertEqual(out["model"], FREE)
        self.assertEqual(out["policy"]["calls"], 1)

    def test_05_legitimate_paid_model_consumes_exactly_one_approval(self):
        aid = self._approval()
        d = self._paid_decision(aid)
        real_consume = gov.consume
        counts = {"n": 0}

        def counted(*a, **kw):
            counts["n"] += 1
            return real_consume(*a, **kw)

        transport, calls = fake_transport(PAID, 0.25)
        with mock.patch.object(gov, "consume", counted), \
             mock.patch.object(ir, "_http", transport), \
             mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("REAL NETWORK")):
            out = pp.dispatch(d, "paid prompt", size="512x512")
        self.assertTrue(out["ok"], out)
        self.assertEqual(counts["n"], 1)
        self.assertEqual(len(calls), 1)
        self.assertTrue(gov.approval_info(aid)["consumed"])
        self.assertTrue(out["policy"]["approval_consumed_once"])
        self.assertFalse(pp.consume_approval(d)["ok"])

    def test_06_price_change_after_decision_is_revalidated(self):
        d = self._free_decision()
        cfg = json.loads(self.cfg.read_text(encoding="utf-8"))
        cfg["providers"][0]["models"][FREE]["estimated_cost_usd"] = 0.30
        cfg["providers"][0]["models"][FREE]["free"] = False
        self.cfg.write_text(json.dumps(cfg), encoding="utf-8")
        transport, calls = fake_transport(FREE, 0.0)
        with mock.patch.object(ir, "_http", transport):
            out = pp.dispatch(d, "free prompt", size="512x512")
        self.assertFalse(out["ok"])
        self.assertIn("paid-model-requires-explicit-approval", out["reason"])
        self.assertEqual(calls, [])

    def test_07_prompt_change_at_dispatch_rejected_before_transport(self):
        d = self._free_decision()
        transport, calls = fake_transport(FREE, 0.0)
        with mock.patch.object(ir, "_http", transport):
            out = pp.dispatch(d, "different prompt", size="512x512")
        self.assertFalse(out["ok"])
        self.assertIn("call-prompt-changed", out["reason"])
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
