"""Phase 02 final correction — true one-shot execution authorization.

Proposal -> explicit confirmation -> atomic consumption are distinct steps.
Real filesystem stores; real concurrent processes for replay; no timing sleeps.
"""
import json
import multiprocessing as mp
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import (confirmations, factory, opstatus, orchestrator,
                  request_intelligence as ri, tool_events, tool_executor,
                  tool_registry)
from core.tests import trusted_request

_CTX = mp.get_context("fork")


def _consume_worker(store, conf, flags, rid, project, goal, plan,
                    barrier, result_path):
    ri._STORE = Path(store)
    confirmations._STORE = Path(conf)
    ri._FLAGS = Path(flags)
    barrier.wait(timeout=15)
    try:
        res = ri.consume_execution(rid, project, goal, plan)
        payload = {"allowed": bool(res["allowed"]), "reason": res["reason"]}
    except BaseException as exc:  # pragma: no cover - diagnostic
        payload = {"allowed": False, "reason": repr(exc)}
    Path(result_path).write_text(json.dumps(payload), encoding="utf-8")


class OneShotAuthorizationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = self.base / "requests.json"
        self.store.write_text(json.dumps({"schema": ri.STORE_VERSION,
                                          "items": {}}), encoding="utf-8")
        self.conf = self.base / "confirmations.json"
        self.conf.write_text(json.dumps({"items": {}}), encoding="utf-8")
        self.flags = self.base / "flags.json"
        self.patches = [mock.patch.object(ri, "_STORE", self.store),
                        mock.patch.object(ri, "_FLAGS", self.flags),
                        mock.patch.object(confirmations, "_STORE", self.conf)]
        for p in self.patches:
            p.start()
        ri.set_compat_mode("canonical")
        d = config.PROJECTS_DIR / "p2-oneshot"
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text('{"name":"p2-oneshot","type":"webapp"}',
                                     encoding="utf-8")
        self.project = "p2-oneshot"
        self.goal = "ساخت صفحه وب ساده"

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _record(self, rid="low-1", goal=None, high=False):
        goal = goal or ("ساخت سیستم پرداخت وب" if high else self.goal)
        rec = ri.intake(goal, project=self.project, request_id=rid)
        rec = ri.build_plan(rid)
        if high:
            prop = ri.approval_proposal(rid)
            ri.approve(rid, prop["confirm_token"], "user-1",
                       project=self.project)
        return ri.get(rid)

    def _grant(self, rec, rid=None, goal=None, actor="user-1"):
        rid = rid or rec["request_id"]
        goal = goal or rec["intent"]["goal"]
        prop = ri.propose_execution(rid, actor, "execute-bounded-plan",
                                    self.project, goal, rec["plan"]["steps"])
        return ri.confirm_execution(rid, prop["confirm_token"], actor,
                                    "execute-bounded-plan", self.project,
                                    goal, rec["plan"]["steps"]), prop

    def _check(self, rec, goal=None, plan=None):
        return ri.can_execute(rec["request_id"], project=self.project,
                              goal=goal or rec["intent"]["goal"],
                              plan=plan if plan is not None else rec["plan"])

    # ------------------------------------------------------ 1-3 trust/proposal
    def test_actor_string_alone_cannot_issue_capability(self):
        rec = self._record()
        with self.assertRaisesRegex(ri.RequestError, "confirmation-required"):
            ri.authorize_execution(rec["request_id"], "fabricated-user",
                                   "execute-bounded-plan", self.project,
                                   self.goal, rec["plan"]["steps"])
        self.assertFalse(self._check(rec)["allowed"])

    def test_proposal_does_not_authorize_execution(self):
        rec = self._record()
        prop = ri.propose_execution(rec["request_id"], "user-1",
                                    "execute-bounded-plan", self.project,
                                    self.goal, rec["plan"]["steps"])
        self.assertTrue(prop["confirm_token"])
        state = ri.get(rec["request_id"])["execution"]["state"]
        self.assertEqual(state, "PROPOSED")
        check = self._check(rec)
        self.assertFalse(check["allowed"])
        self.assertEqual(check["reason"], "no-execution-authorization")

    def test_explicit_confirmation_required_before_issuance(self):
        rec = self._record()
        ri.propose_execution(rec["request_id"], "user-1",
                             "execute-bounded-plan", self.project,
                             self.goal, rec["plan"]["steps"])
        with self.assertRaisesRegex(ri.RequestError, "token-mismatch"):
            ri.confirm_execution(rec["request_id"], "bogus", "user-1",
                                 "execute-bounded-plan", self.project,
                                 self.goal, rec["plan"]["steps"])
        self.assertFalse(self._check(rec)["allowed"])
        self._grant(rec)
        self.assertTrue(self._check(rec)["allowed"])
        granted = ri.get(rec["request_id"])["execution"]
        self.assertTrue(granted["consent_confirmed"])
        self.assertFalse(granted["actor_trusted"])
        self.assertEqual(granted["identity_mode"], "self-declared")

    # ------------------------------------------------------------ 4-7 one-shot
    def test_valid_capability_succeeds_exactly_once(self):
        rec = self._record()
        self._grant(rec)
        first = ri.consume_execution(rec["request_id"], self.project,
                                     self.goal, rec["plan"])
        self.assertTrue(first["allowed"])
        self.assertEqual(first["reason"], "capability-consumed")
        self.assertEqual(ri.get(rec["request_id"])["execution"]["state"],
                         "CONSUMED")
        second = ri.consume_execution(rec["request_id"], self.project,
                                      self.goal, rec["plan"])
        self.assertFalse(second["allowed"])
        self.assertEqual(second["reason"], "no-execution-authorization")

    def test_second_sequential_attempt_fails(self):
        rec = self._record("seq-1")
        self._grant(rec)
        with mock.patch.object(factory, "generate") as generate:
            first = ri.consume_execution(rec["request_id"], self.project,
                                         self.goal, rec["plan"])
            self.assertTrue(first["allowed"])
            factory.generate(self.project, self.goal)  # the one permitted action
            second = ri.consume_execution(rec["request_id"], self.project,
                                          self.goal, rec["plan"])
            self.assertFalse(second["allowed"])
            self.assertEqual(second["reason"], "no-execution-authorization")
            self.assertEqual(generate.call_count, 1)

    def test_two_processes_cannot_both_consume(self):
        rec = self._record("mp-1")
        self._grant(rec)
        barrier = _CTX.Barrier(2)
        paths = [self.base / ("mp-%d.json" % i) for i in range(2)]
        procs = []
        for i in range(2):
            p = _CTX.Process(target=_consume_worker, args=(
                str(self.store), str(self.conf), str(self.flags),
                rec["request_id"], self.project, self.goal, rec["plan"],
                barrier, str(paths[i])))
            p.start()
            procs.append(p)
        for p in procs:
            p.join(timeout=30)
            self.assertFalse(p.is_alive())
        results = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
        self.assertEqual([r["allowed"] for r in results].count(True), 1)
        self.assertEqual([r["allowed"] for r in results].count(False), 1)
        self.assertEqual(ri.get(rec["request_id"])["execution"]["state"],
                         "CONSUMED")

    def test_consumed_capability_unusable_after_reload(self):
        rec = self._record("reload-1")
        self._grant(rec)
        ri.consume_execution(rec["request_id"], self.project, self.goal,
                             rec["plan"])
        reloaded = ri.get(rec["request_id"])
        self.assertEqual(reloaded["execution"]["state"], "CONSUMED")
        self.assertFalse(ri.consume_execution(
            rec["request_id"], self.project, self.goal,
            reloaded["plan"])["allowed"])

    # ------------------------------------------------------------- 8 failures
    def test_expired_stale_mismatched_and_altered_fail_closed(self):
        rec = self._record("fail-1")
        self._grant(rec)
        # expired (authoritative timestamp in canonical record)
        d = json.loads(self.store.read_text(encoding="utf-8"))
        d["items"][rec["request_id"]]["execution"]["expires"] = 1
        self.store.write_text(json.dumps(d), encoding="utf-8")
        self.assertEqual(ri.consume_execution(
            rec["request_id"], self.project, self.goal,
            rec["plan"])["reason"], "execution-authorization-expired")
        # restore, then stale via a material answer
        d = json.loads(self.store.read_text(encoding="utf-8"))
        d["items"][rec["request_id"]]["execution"]["expires"] = 9999999999
        self.store.write_text(json.dumps(d), encoding="utf-8")
        ri.answer(rec["request_id"], "اندروید", "platform")
        self.assertEqual(ri.consume_execution(
            rec["request_id"], self.project, self.goal,
            rec["plan"])["reason"], "execution-authorization-stale")
        # fresh capability for mismatch cases
        rec2 = self._record("fail-2")
        self._grant(rec2)
        self.assertEqual(ri.consume_execution(
            rec2["request_id"], "other-project", self.goal,
            rec2["plan"])["reason"], "execution-project-ownership-mismatch")
        self.assertEqual(ri.consume_execution(
            rec2["request_id"], self.project, "ساخت بازی اندرویدی",
            rec2["plan"])["reason"], "execution-goal-mismatch")
        changed = json.loads(json.dumps(rec2["plan"]))
        changed["steps"] = list(reversed(changed["steps"]))
        self.assertEqual(ri.consume_execution(
            rec2["request_id"], self.project, self.goal,
            changed)["reason"], "execution-steps-mismatch")
        self.assertEqual(ri.consume_execution(
            rec2["request_id"], self.project, self.goal,
            None)["reason"], "bounded-plan-required")
        self.assertEqual(ri.consume_execution(
            rec2["request_id"], self.project, self.goal, rec2["plan"],
            scope="unrestricted")["reason"], "execution-scope-mismatch")
        self.assertEqual(ri.consume_execution(
            rec2["request_id"], self.project, self.goal, rec2["plan"],
            session_id="bogus-session")["reason"], "execution-session-invalid")
        # all failures left the capability unconsumed
        self.assertTrue(ri.consume_execution(
            rec2["request_id"], self.project, self.goal,
            rec2["plan"])["allowed"])

    def test_corrupt_store_prevents_consumption_and_side_effects(self):
        rec = self._record("corrupt-1")
        self._grant(rec)
        self.store.write_text("{broken", encoding="utf-8")
        with mock.patch.object(factory, "generate",
                               side_effect=AssertionError("side effect")):
            res = ri.consume_execution(rec["request_id"], self.project,
                                       self.goal, rec["plan"])
        self.assertFalse(res["allowed"])

    def test_persistence_failure_prevents_consumption(self):
        rec = self._record("persist-1")
        self._grant(rec)
        with mock.patch.object(ri, "_save_unlocked",
                               side_effect=ri.RequestStoreError("write-failed")):
            res = ri.consume_execution(rec["request_id"], self.project,
                                       self.goal, rec["plan"])
        self.assertFalse(res["allowed"])
        self.assertIn("persistence-failed", res["reason"])

    # --------------------------------------------------- 9-10 high/critical
    def test_high_approval_without_execution_authorization_cannot_execute(self):
        rec = self._record("high-1", high=True)
        self.assertEqual(ri.get(rec["request_id"])["status"], "APPROVED")
        self.assertFalse(self._check(rec)["allowed"])
        self.assertFalse(ri.consume_execution(
            rec["request_id"], self.project, "ساخت سیستم پرداخت وب",
            rec["plan"])["allowed"])

    def test_high_approval_plus_authorization_allows_one_consumption(self):
        rec = self._record("high-2", high=True)
        self._grant(rec, goal="ساخت سیستم پرداخت وب")
        goal = "ساخت سیستم پرداخت وب"
        self.assertTrue(ri.consume_execution(
            rec["request_id"], self.project, goal, rec["plan"])["allowed"])
        self.assertFalse(ri.consume_execution(
            rec["request_id"], self.project, goal, rec["plan"])["allowed"])

    # -------------------------------------------------------- 12-14 boundaries
    def test_boundaries_fail_closed_without_bounded_adapter(self):
        for i, goal in enumerate((self.goal, "...", "ساخت سیستم پرداخت وب")):
            with self.subTest(goal=goal):
                g = orchestrator.canonical_execution_gate(
                    "t-%d" % i, self.project, goal, plan=None)
                self.assertFalse(g["allowed"])
        with mock.patch.object(factory, "generate",
                               side_effect=AssertionError("side effect")):
            g = orchestrator.canonical_execution_gate(
                "t-direct", self.project, self.goal, plan=None)
        self.assertFalse(g["allowed"])

    def test_legacy_switch_is_opt_in_only(self):
        with mock.patch.object(ri, "intake",
                               side_effect=ri.RequestStoreError("corrupt")):
            self.assertFalse(orchestrator.canonical_execution_gate(
                "t-legacy-block", self.project, self.goal)["allowed"])
        ri.set_compat_mode("legacy")
        try:
            self.assertTrue(orchestrator.canonical_execution_gate(
                "t-legacy-ok", self.project, self.goal)["allowed"])
        finally:
            ri.set_compat_mode("canonical")

    def test_phase01_idempotency_still_holds(self):
        ev = self.base / "events.json"
        rc = self.base / "receipts.json"
        ev.write_text("[]", encoding="utf-8")
        rc.write_text("{}", encoding="utf-8")
        with mock.patch.object(tool_events, "_STORE", ev), \
             mock.patch.object(tool_events, "_RECEIPT_STORE", rc):
            tool_executor._seen_calls.clear()
            tool_executor._seen_sigs.clear()
            tid = "p2-one-" + str(id(self))
            tool_registry.registry().register(tool_registry.ToolSpec(
                tid, "P2", "test", "test", "test", "project:read", {},
                timeout_s=10, adapter=lambda p, i, **k: {"ok": True}))
            req = trusted_request(tool_id=tid, project=self.project,
                                            inputs={}, scopes=["project:read"],
                                            call_id="p2-one-shot-check")
            self.assertEqual(tool_executor.execute(req).status, opstatus.PASS)
            self.assertEqual(tool_executor.execute(req).status, opstatus.BLOCKED)


if __name__ == "__main__":
    unittest.main()
