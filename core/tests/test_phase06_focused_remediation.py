"""Phase 06 focused remediation tests — ADR-012 dispatch/approval + ADR-013 isolation.

All provider transport calls here are MOCKED. No real network request, paid
call or credit spend occurs in this file.
"""
import base64
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import core.config as config
from core import (image_governance as gov, image_providers as ip,
                  image_remote as ir, provider_policy as pp, visual_review as vr)

PROJECT = "p06-focus"
FREE = "recraft/recraft-v4.1-flash-v2:free"
PAID = "openrouter/example-paid-model"
BUF = io.BytesIO()
Image.new("RGBA", (32, 32), (20, 40, 80, 255)).save(BUF, "PNG")
PNG = BUF.getvalue()
B64 = base64.b64encode(PNG).decode()


def http_ok(model, cost=0.0):
    calls = []

    def handler(method, url, headers, body, timeout):
        calls.append((method, url, body))
        payload = {"model": model, "data": [{"b64_json": B64,
                                             "media_type": "image/png"}],
                   "usage": {"cost": cost}}
        return 200, {}, json.dumps(payload).encode()
    return handler, calls


class FocusedRemediationTest(unittest.TestCase):
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
        patches = [
            mock.patch.object(pp, "_CONFIG", self.cfg),
            mock.patch.object(ip, "_CONFIG", self.cfg),
            mock.patch.object(gov, "_CONFIG", self.cfg),
            mock.patch.object(gov, "_STORE", self.store),
            mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-only"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _approve(self, model, prompt="selected prompt", size="512x512"):
        payload = {"provider": "openrouter-image", "model": model,
                   "quality": "standard", "prompt": prompt, "size": size,
                   "references": [], "operation": "generate",
                   "project": PROJECT}
        r = gov.request_approval(PROJECT, gov.payload_from(PROJECT, payload),
                                 "tester")
        self.assertEqual(r["status"], "PASS", r)
        self.assertEqual(gov.decide(r["approval_id"], "approve",
                                    "tester")["status"], "PASS")
        return r["approval_id"]

    # ------------------------------------------------------------ A: dispatch
    def test_A1_selected_free_model_reaches_adapter_unchanged(self):
        handler, calls = http_ok(FREE, 0.0)
        d = pp.decide(PROJECT, "openrouter-image", FREE,
                      prompt="exact prompt", size="512x512")
        self.assertTrue(d["ok"], d)
        with mock.patch.object(ir, "_http", handler), \
             mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("REAL NETWORK")):
            out = pp.dispatch(d, "exact prompt", size="512x512")
        self.assertTrue(out["ok"], out)
        self.assertEqual(len(calls), 1)
        body = calls[0][2]
        self.assertEqual((json.loads(body) if isinstance(body, (str, bytes))
                          else body)["model"], FREE)
        self.assertEqual(out["model"], FREE)
        self.assertEqual(out["policy"]["model"], FREE)
        self.assertEqual(out["policy"]["calls"], 1)
        self.assertFalse(out["policy"]["fallback"])

    def test_A2_paid_model_explicit_selection_and_cost(self):
        blocked = pp.decide(PROJECT, "openrouter-image", PAID,
                            prompt="paid", size="512x512")
        self.assertFalse(blocked["ok"])
        self.assertIn("paid-model-requires-explicit-approval", blocked["reason"])
        self.assertEqual(blocked["estimated_cost_usd"], 0.25)
        aid = self._approve(PAID, prompt="paid", size="512x512")
        d = pp.decide(PROJECT, "openrouter-image", PAID, prompt="paid",
                      size="512x512", approval_id=aid,
                      confirmed_cost_usd="0.25")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["model"], PAID)
        handler, calls = http_ok(PAID, 0.25)
        with mock.patch.object(ir, "_http", handler), \
             mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("REAL NETWORK")):
            out = pp.dispatch(d, "paid", size="512x512")
        self.assertTrue(out["ok"], out)
        body = calls[0][2]
        self.assertEqual((json.loads(body) if isinstance(body, (str, bytes))
                          else body)["model"], PAID)
        self.assertEqual(out["model"], PAID)

    def test_A3_no_automatic_provider_or_model_selection(self):
        with mock.patch.object(ip, "active",
                               side_effect=AssertionError("AUTO SELECT")), \
             mock.patch.object(ip, "select",
                               side_effect=AssertionError("AUTO SELECT")):
            d = pp.decide(PROJECT, "openrouter-image", FREE, prompt="x")
            self.assertTrue(d["ok"], d)
        handler, calls = http_ok(FREE, 0.0)
        with mock.patch.object(ir, "_http", handler):
            out = pp.dispatch(d, "x")
        self.assertTrue(out["ok"], out)
        self.assertEqual(len(calls), 1)

    def test_A4_returned_model_mismatch_is_rejected(self):
        handler, _ = http_ok("other/model", 0.0)
        d = pp.decide(PROJECT, "openrouter-image", FREE, prompt="x")
        with mock.patch.object(ir, "_http", handler):
            out = pp.dispatch(d, "x")
        self.assertFalse(out["ok"])
        self.assertIn("returned-model-mismatch", out.get("error", ""))

    def test_A5_unverified_price_blocks_before_any_call(self):
        cfg = json.loads(self.cfg.read_text(encoding="utf-8"))
        cfg["providers"][0]["models"][PAID]["estimated_cost_usd"] = None
        self.cfg.write_text(json.dumps(cfg), encoding="utf-8")
        handler, calls = http_ok(FREE, 0.0)
        with mock.patch.object(ir, "_http", handler):
            d = pp.decide(PROJECT, "openrouter-image", PAID, prompt="x")
        self.assertFalse(d["ok"])
        self.assertIn("price-not-verified", d["reason"])
        self.assertEqual(calls, [])

    # --------------------------------------------------------- B: one-time
    def test_B1_paid_approval_consumed_once_with_second_consume_in_adapter(self):
        aid = self._approve(PAID, prompt="paid")
        d = pp.decide(PROJECT, "openrouter-image", PAID, prompt="paid",
                      size="512x512", approval_id=aid, confirmed_cost_usd="0.25")
        self.assertTrue(d["ok"], d)
        real_consume = gov.consume
        counts = {"n": 0}

        def counted(*a, **kw):
            counts["n"] += 1
            return real_consume(*a, **kw)

        handler, calls = http_ok(PAID, 0.25)
        with mock.patch.object(gov, "consume", counted), \
             mock.patch.object(ir, "_http", handler):
            out = pp.dispatch(d, "paid", size="512x512")
        self.assertTrue(out["ok"], out)
        self.assertEqual(counts["n"], 1)
        self.assertEqual(len(calls), 1)
        self.assertTrue(gov.approval_info(aid)["consumed"])
        self.assertTrue(out["policy"]["approval_consumed_once"])
        replay = pp.consume_approval(d)
        self.assertFalse(replay["ok"])
        self.assertIn(replay["reason"],
                      ("decision-already-consumed", "approval-already-used"))
        self.assertEqual(counts["n"], 1)

    def test_B2_adapter_replay_of_consumed_approval_is_blocked(self):
        aid = self._approve(PAID, prompt="paid")
        req = {"model": PAID, "project": PROJECT, "provider": "openrouter-image",
               "approval_id": aid, "confirmed_cost_usd": "0.25",
               "operation": "generate", "prompt": "paid"}
        d = pp.decide(PROJECT, "openrouter-image", PAID, prompt="paid",
                      size="512x512", approval_id=aid,
                      confirmed_cost_usd="0.25")
        with mock.patch.object(ir, "_http", http_ok(PAID, 0.25)[0]):
            self.assertTrue(pp.dispatch(d, "paid", size="512x512")["ok"])
        with mock.patch.object(ir, "_http",
                               side_effect=AssertionError("NETWORK")):
            r = ip.get("openrouter-image").generate(req.pop("prompt"), **req)
        self.assertEqual(r.status, "BLOCKED")
        self.assertIn("approval-already-used", r.error)

    def test_B3_concurrent_reuse_rejected(self):
        aid = self._approve(PAID, prompt="paid")
        d = pp.decide(PROJECT, "openrouter-image", PAID, prompt="paid",
                      size="512x512", approval_id=aid,
                      confirmed_cost_usd="0.25")
        barrier = threading.Barrier(4)
        results = []

        def work():
            barrier.wait(timeout=10)
            results.append(pp.consume_approval(d)["ok"])

        threads = [threading.Thread(target=work) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(len(results), 4)
        self.assertEqual(sum(1 for r in results if r), 1)

    def test_B4_changed_prompt_cost_or_model_rejected(self):
        aid = self._approve(PAID, prompt="original")
        changed = pp.decide(PROJECT, "openrouter-image", PAID,
                            prompt="TAMPERED", size="512x512", approval_id=aid,
                            confirmed_cost_usd="0.25")
        self.assertFalse(changed["ok"])
        self.assertIn("paid-approval-request-mismatch", changed["reason"])
        wrong_cost = pp.decide(PROJECT, "openrouter-image", PAID,
                               prompt="original", size="512x512", approval_id=aid,
                               confirmed_cost_usd="0.99")
        self.assertFalse(wrong_cost["ok"])
        self.assertIn("paid-cost-mismatch", wrong_cost["reason"])
        other = pp.decide(PROJECT, "openai-image", PAID, prompt="original",
                          size="512x512", approval_id=aid,
                          confirmed_cost_usd="0.25")
        self.assertFalse(other["ok"])

    def test_B5_expired_approval_rejected(self):
        aid = self._approve(PAID, prompt="paid")
        data = json.loads(self.store.read_text(encoding="utf-8"))
        data["approvals"][aid]["at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 3600))
        self.store.write_text(json.dumps(data), encoding="utf-8")
        d = pp.decide(PROJECT, "openrouter-image", PAID, prompt="paid",
                      size="512x512", approval_id=aid, confirmed_cost_usd="0.25")
        self.assertFalse(d["ok"])
        self.assertIn("paid-approval-expired", d["reason"])

    # ------------------------------------------------------- C: isolation
    def test_C1_unavailable_os_isolation_blocks_browser_entirely(self):
        def probe_or_browser(cmd, **kw):
            if len(cmd) >= 3 and cmd[1] == "-n" and cmd[2] == "true":
                return mock.Mock(returncode=1, stdout="", stderr="Operation not permitted")
            raise AssertionError("BROWSER STARTED WITHOUT ISOLATION")

        with mock.patch.object(vr.subprocess, "run", side_effect=probe_or_browser):
            cap = vr.network_isolation_capability()
            self.assertEqual(cap["status"], "BLOCKED")
            out = vr.capture(PROJECT, "file://" + str(
                config.PROJECTS_DIR / PROJECT / "meta.json"))
        self.assertFalse(out["ok"])
        self.assertIn("network-isolation-unavailable", out["reason"])

    def test_C2_isolated_command_denies_network_and_resources(self):
        iso = {"status": "AVAILABLE", "mechanism": "unshare -n",
               "path": "/fake-unshare", "limits": []}
        browser = {"status": "AVAILABLE", "tool": "chromium", "path": "/fake-chromium",
                   "limits": []}
        seen = {}

        def fake(cmd, **kw):
            seen["cmd"] = cmd
            seen["env"] = kw.get("env", {})
            out = cmd[-2].split("=", 1)[1]
            Path(out).write_bytes(PNG)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(vr, "network_isolation_capability", return_value=iso), \
             mock.patch.object(vr, "browser_capability", return_value=browser), \
             mock.patch.object(vr.subprocess, "run", side_effect=fake):
            r = vr.capture(PROJECT, "about:blank", "evidence/focus.png")
        self.assertTrue(r["ok"], r)
        cmd = seen["cmd"]
        self.assertEqual(cmd[:2], ["/fake-unshare", "-n"])
        self.assertIn("/fake-chromium", cmd)
        self.assertTrue(any("host-resolver-rules" in x for x in cmd))
        self.assertTrue(any("no-proxy-server" in x for x in cmd))
        self.assertTrue(any("disable-background-networking" in x for x in cmd))
        self.assertFalse(any(x.startswith("http://") or x.startswith("https://")
                             for x in cmd))
        self.assertFalse(any("SUPERSECRET" in str(v)
                             for v in seen["env"].values()))
        self.assertFalse(r["production_ready"])

    def test_C3_external_target_blocked_even_with_isolation(self):
        iso = {"status": "AVAILABLE", "mechanism": "unshare -n",
               "path": "/fake-unshare", "limits": []}
        browser = {"status": "AVAILABLE", "tool": "chromium", "path": "/fake",
                   "limits": []}
        with mock.patch.object(vr, "network_isolation_capability", return_value=iso), \
             mock.patch.object(vr, "browser_capability", return_value=browser), \
             mock.patch.object(vr.subprocess, "run",
                               side_effect=AssertionError("BROWSER STARTED")):
            out = vr.capture(PROJECT, "https://example.test/a.png")
        self.assertFalse(out["ok"])
        self.assertIn("target", out["reason"])


if __name__ == "__main__":
    unittest.main()
