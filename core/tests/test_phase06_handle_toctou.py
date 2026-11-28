"""Phase 06 — deterministic decision-handle TOCTOU closure tests.

All transport calls are MOCKED; no live provider request and no spend.
Races are forced with threading.Event / Barrier, never with random sleeps.
"""
import base64
import io
import json
import multiprocessing
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import core.config as config
from core import (image_governance as gov, image_providers as ip,
                  image_remote as ir, provider_policy as pp)

PROJECT = "p06-toctou"
FREE = "recraft/recraft-v4.1-flash-v2:free"
PAID = "openrouter/example-paid-model"
BUF = io.BytesIO()
Image.new("RGBA", (16, 16), (10, 20, 30, 255)).save(BUF, "PNG")
B64 = base64.b64encode(BUF.getvalue()).decode()


def transport(model, cost, fail=False):
    calls = []

    def handler(method, url, headers, body, timeout):
        calls.append(body)
        if fail:
            raise RuntimeError("mock-transport-failure")
        return 200, {}, json.dumps({
            "model": model, "data": [{"b64_json": B64}],
            "usage": {"cost": cost}}).encode()
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


class HandleTOCTOUTest(unittest.TestCase):
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

    def _free(self, prompt="p"):
        return pp.decide(PROJECT, "openrouter-image", FREE, prompt=prompt)

    def _paid(self):
        payload = {"provider": "openrouter-image", "model": PAID,
                   "quality": "standard", "prompt": "paid", "size": "",
                   "references": [], "operation": "generate",
                   "project": PROJECT}
        a = gov.request_approval(PROJECT, gov.payload_from(PROJECT, payload),
                                 "tester")
        gov.decide(a["approval_id"], "approve", "tester")
        return pp.decide(PROJECT, "openrouter-image", PAID, prompt="paid",
                         approval_id=a["approval_id"],
                         confirmed_cost_usd="0.25"), a["approval_id"]

    def _flags(self):
        return json.loads(pp._DECISIONS_STORE.read_text(
            encoding="utf-8"))["decisions"]

    # ------------------------------------------------------- forced races
    def test_01_id_mutated_after_validation_cannot_redirect_consumption(self):
        a = self._free("A")
        pristine_a = dict(a)
        b = self._free("B")
        b_id = b["decision_id"]
        reached_validate = threading.Event()
        mutation_done = threading.Event()
        real_validate = pp._validate_request

        def gated_validate(req):
            reached_validate.set()
            mutation_done.wait(timeout=10)   # deterministic handoff
            return real_validate(req)

        def mutator():
            self.assertTrue(reached_validate.wait(timeout=10))
            a["decision_id"] = b_id          # mutate AFTER peek/validation gate
            mutation_done.set()

        thread = threading.Thread(target=mutator)
        thread.start()
        with mock.patch.object(pp, "_validate_request", gated_validate):
            gate = pp.consume_approval(a)
        thread.join(timeout=10)
        self.assertTrue(gate["ok"], gate)
        flags = self._flags()
        self.assertTrue(flags[pristine_a["decision_id"]]["consumed"])
        self.assertFalse(flags[b_id]["consumed"])              # B untouched
        self.assertEqual(pp.consume_approval(pristine_a)["reason"],
                         "decision-already-consumed")
        self.assertTrue(pp.consume_approval(b)["ok"])

    def test_02_id_mutated_between_peek_and_consume_is_rejected(self):
        a = self._free("A")
        b = self._free("B")
        pristine = dict(a)
        pristine_id = pristine["decision_id"]
        b_id = b["decision_id"]
        real_peek = pp._peek_decision

        def mutating_peek(decision):
            rec, problem = real_peek(decision)
            decision["decision_id"] = b["decision_id"]
            return rec, problem

        with mock.patch.object(pp, "_peek_decision", mutating_peek):
            out = pp.consume_approval(a)
        # The caller dict changed, but consumption MUST use the verified
        # server-side handle, so the ORIGINAL decision is the one consumed.
        self.assertTrue(out["ok"], out)
        flags = self._flags()
        self.assertTrue(flags[pristine_id]["consumed"])
        self.assertFalse(flags[b_id]["consumed"])
        self.assertEqual(pp.consume_approval(pristine)["reason"],
                         "decision-already-consumed")

    def test_03_consume_A_with_B_id_rejected(self):
        a = self._free("A")
        b = self._free("B")
        forged = dict(a, decision_id=b["decision_id"])
        out = pp.consume_approval(forged)
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "decision-mutated-after-validation")
        flags = self._flags()
        self.assertFalse(flags[a["decision_id"]]["consumed"])
        self.assertFalse(flags[b["decision_id"]]["consumed"])
        self.assertTrue(pp.consume_approval(b)["ok"])

    def test_04_threads_and_processes_one_winner_per_handle(self):
        decision = self._free("race")
        barrier = threading.Barrier(4)
        results = []

        def work():
            barrier.wait(timeout=10)          # deterministic simultaneous start
            results.append(pp.consume_approval(dict(decision)))

        threads = [threading.Thread(target=work) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        self.assertEqual(len(results), 4)
        self.assertEqual(sum(1 for r in results if r["ok"]), 1, results)

        d2 = self._free("race-proc")
        queue = multiprocessing.Queue()
        procs = [multiprocessing.Process(
            target=_worker, args=(d2, str(self.cfg), str(self.store), queue))
            for _ in range(4)]
        for p in procs:
            p.start()
        replies = [queue.get(timeout=40) for _ in procs]
        for p in procs:
            p.join(timeout=40)
        self.assertEqual(sum(1 for _, r in replies
                             if isinstance(r, dict) and r.get("ok")), 1,
                         replies)
        self.assertEqual(self._flags()[d2["decision_id"]]["consumed"], True)

    # --------------------------------------------------- preserved contracts
    def test_05_free_and_paid_one_shot_preserved(self):
        free = self._free("free")
        handler, calls = transport(FREE, 0.0)
        with mock.patch.object(ir, "_http", handler):
            first = pp.dispatch(free, "free")
            second = pp.dispatch(free, "free")
        self.assertTrue(first["ok"], first)
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason"], "decision-already-consumed")
        self.assertEqual(len(calls), 1)

        paid, aid = self._paid()
        h2, calls2 = transport(PAID, 0.25)
        with mock.patch.object(ir, "_http", h2):
            good = pp.dispatch(paid, "paid")
            replay = pp.dispatch(paid, "paid")
        self.assertTrue(good["ok"], good)
        self.assertFalse(replay["ok"])
        self.assertEqual(len(calls2), 1)
        self.assertTrue(gov.approval_info(aid).get("consumed"))

    def test_06_replay_after_provider_failure_rejected_no_second_call(self):
        d = self._free("fail")
        handler, calls = transport(FREE, 0.0, fail=True)
        with mock.patch.object(ir, "_http", handler):
            first = pp.dispatch(d, "fail")
            second = pp.dispatch(d, "fail")
        self.assertFalse(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason"], "decision-already-consumed")
        self.assertEqual(len(calls), 1)

    def test_07_no_transport_on_any_rejected_path(self):
        a = self._free("A")
        b = self._free("B")
        handler, calls = transport(FREE, 0.0)
        with mock.patch.object(ir, "_http", handler):
            pp.dispatch(dict(a, decision_id=b["decision_id"]), "A")
            pp.dispatch({"decision_id": "bogus"}, "A")
            pp.dispatch(dict(a, free=False), "A")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
