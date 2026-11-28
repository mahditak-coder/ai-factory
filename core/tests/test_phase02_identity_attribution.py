"""Phase 02 final — authoritative identity attribution from panel sessions.

The caller's actor label is never authenticated by mere session validity.
Real isolated stores; real multi-process replay for one-shot integrity.
"""
import json
import multiprocessing as mp
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import confirmations, panelauth, request_intelligence as ri

_CTX = mp.get_context("fork")
VALID = "valid-panel-session"


def _consume_worker(store, conf, flags, rid, project, goal, plan,
                    barrier, result_path):
    ri._STORE = Path(store)
    confirmations._STORE = Path(conf)
    ri._FLAGS = Path(flags)
    barrier.wait(timeout=15)
    try:
        r = ri.consume_execution(rid, project, goal, plan, session_id=VALID)
        payload = {"allowed": bool(r["allowed"]), "reason": r["reason"]}
    except BaseException as exc:  # pragma: no cover - diagnostic
        payload = {"allowed": False, "reason": repr(exc)}
    Path(result_path).write_text(json.dumps(payload), encoding="utf-8")


class IdentityAttributionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = self.base / "requests.json"
        self.store.write_text(json.dumps({"schema": ri.STORE_VERSION,
                                          "items": {}}), encoding="utf-8")
        self.conf = self.base / "confirmations.json"
        self.conf.write_text(json.dumps({"items": {}}), encoding="utf-8")
        self.flags = self.base / "flags.json"
        self.patches = [
            mock.patch.object(ri, "_STORE", self.store),
            mock.patch.object(ri, "_FLAGS", self.flags),
            mock.patch.object(confirmations, "_STORE", self.conf),
            mock.patch.object(panelauth, "principal_for_session",
                              side_effect=lambda t: (panelauth.PANEL_PRINCIPAL
                                                     if t == VALID else None)),
        ]
        for p in self.patches:
            p.start()
        d = config.PROJECTS_DIR / "p2-identity"
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text('{"name":"p2-identity","type":"webapp"}',
                                     encoding="utf-8")
        self.project = "p2-identity"
        self.goal = "ساخت صفحه وب ساده"

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _low(self, rid, goal=None):
        rec = ri.intake(goal or self.goal, project=self.project, request_id=rid)
        return ri.build_plan(rid)

    def _propose(self, rec, session=VALID, actor="user-1"):
        return ri.propose_execution(rec["request_id"], actor,
                                    "execute-bounded-plan", self.project,
                                    rec["intent"]["goal"], rec["plan"]["steps"],
                                    session_id=session)

    def _confirm(self, rec, token, session=VALID, actor="user-1"):
        return ri.confirm_execution(rec["request_id"], token, actor,
                                    "execute-bounded-plan", self.project,
                                    rec["intent"]["goal"], rec["plan"]["steps"],
                                    session_id=session)

    # ------------------------------------------------------- identity binding
    def test_valid_session_fabricated_actor_never_authenticated(self):
        rec = self._low("id-fabricated")
        prop = self._propose(rec, actor="totally-fabricated")
        granted = self._confirm(rec, prop["confirm_token"],
                                actor="totally-fabricated")
        self.assertTrue(granted["actor_trusted"])
        self.assertEqual(granted["actor"], panelauth.PANEL_PRINCIPAL)
        self.assertEqual(granted["declared_actor"], "totally-fabricated")
        self.assertNotEqual(granted["actor"], "totally-fabricated")
        self.assertEqual(granted["identity_mode"], "authenticated")

    def test_valid_session_resolves_authoritative_principal(self):
        rec = self._low("id-principal")
        prop = self._propose(rec)
        granted = self._confirm(rec, prop["confirm_token"])
        self.assertEqual(granted["authenticated_principal"],
                         panelauth.PANEL_PRINCIPAL)
        self.assertEqual(granted["actor"], panelauth.PANEL_PRINCIPAL)

    def test_missing_invalid_expired_mismatched_session_not_trusted(self):
        rec = self._low("id-missing")
        prop = self._propose(rec, session=VALID)
        with self.assertRaisesRegex(ri.RequestError,
                                    "session-binding-required"):
            self._confirm(rec, prop["confirm_token"], session="")
        with self.assertRaisesRegex(ri.RequestError,
                                    "session-binding-required"):
            self._confirm(rec, prop["confirm_token"], session="other-session")
        rec2 = self._low("id-expired")
        prop2 = self._propose(rec2, session=VALID)
        with mock.patch.object(panelauth, "principal_for_session",
                               return_value=None):
            with self.assertRaisesRegex(ri.RequestError, "session-invalid"):
                self._confirm(rec2, prop2["confirm_token"], session=VALID)
        self.assertEqual(ri.get(rec2["request_id"])["execution"]["state"],
                         "PROPOSED")

    def test_anonymous_confirmation_is_consent_only(self):
        rec = self._low("id-anon")
        prop = self._propose(rec, session="")
        granted = self._confirm(rec, prop["confirm_token"], session="")
        self.assertTrue(granted["consent_confirmed"])
        self.assertFalse(granted["actor_trusted"])
        self.assertEqual(granted["authenticated_principal"], "")
        self.assertEqual(granted["identity_mode"], "self-declared")
        self.assertEqual(granted["actor"], "user-1")

    def test_high_critical_approval_unchanged(self):
        rec = self._low("id-high", "ساخت سیستم پرداخت وب")
        with self.assertRaisesRegex(ri.RequestError,
                                    "explicit-approval-required-for-execution"):
            self._propose(rec)
        prop = ri.approval_proposal(rec["request_id"])
        ri.approve(rec["request_id"], prop["confirm_token"], "user-1",
                   project=self.project)
        exec_prop = self._propose(rec)
        granted = self._confirm(rec, exec_prop["confirm_token"])
        self.assertTrue(granted["consent_confirmed"])

    # ----------------------------------------------------------- one-shot
    def test_sequential_replay_after_trusted_identity(self):
        rec = self._low("id-seq")
        prop = self._propose(rec)
        self._confirm(rec, prop["confirm_token"])
        first = ri.consume_execution(rec["request_id"], self.project, self.goal,
                                     rec["plan"], session_id=VALID)
        self.assertTrue(first["allowed"])
        second = ri.consume_execution(rec["request_id"], self.project, self.goal,
                                      rec["plan"], session_id=VALID)
        self.assertFalse(second["allowed"])
        self.assertEqual(ri.get(rec["request_id"])["execution"]["state"],
                         "CONSUMED")

    def test_two_processes_cannot_consume_same_capability(self):
        rec = self._low("id-mp")
        prop = self._propose(rec)
        self._confirm(rec, prop["confirm_token"])
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
        results = [json.loads(x.read_text(encoding="utf-8")) for x in paths]
        self.assertEqual([r["allowed"] for r in results].count(True), 1)
        self.assertEqual([r["allowed"] for r in results].count(False), 1)

    def test_api_view_exposes_principal_but_no_session_or_token(self):
        rec = self._low("id-view")
        prop = self._propose(rec)
        self._confirm(rec, prop["confirm_token"])
        view = ri.save_view(ri.get(rec["request_id"]))
        blob = json.dumps(view, ensure_ascii=False)
        self.assertNotIn("token", blob)
        self.assertNotIn(VALID, blob)
        self.assertNotIn("session_id", blob)
        self.assertEqual(view["execution"]["authenticated_principal"],
                         panelauth.PANEL_PRINCIPAL)
        self.assertEqual(view["execution"]["declared_actor"], "user-1")


if __name__ == "__main__":
    unittest.main()
