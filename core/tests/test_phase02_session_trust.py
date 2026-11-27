"""Phase 02 final consistency — session binding, project ownership, consent vs identity.

Valid panel sessions are simulated by patching the single authority function;
all canonical and confirmation stores are real isolated temp files.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import confirmations, panelauth, request_intelligence as ri


class SessionTrustTest(unittest.TestCase):
    VALID = "valid-session-token"

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
                        mock.patch.object(confirmations, "_STORE", self.conf),
                        mock.patch.object(
                            panelauth, "session_token_valid",
                            side_effect=lambda token: token == self.VALID)]
        for p in self.patches:
            p.start()
        d = config.PROJECTS_DIR / "p2-session"
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text('{"name":"p2-session","type":"webapp"}',
                                     encoding="utf-8")
        self.project = "p2-session"
        self.goal = "ساخت صفحه وب ساده"

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _low(self, rid="low-1", goal=None):
        rec = ri.intake(goal or self.goal, project=self.project, request_id=rid)
        return ri.build_plan(rid)

    def _high(self, rid="high-1"):
        return self._low(rid, "ساخت سیستم پرداخت وب")

    def _propose(self, rec, rid=None, project=None, session=""):
        rid = rid or rec["request_id"]
        return ri.propose_execution(rid, "user-1", "execute-bounded-plan",
                                    project or self.project,
                                    rec["intent"]["goal"], rec["plan"]["steps"],
                                    session_id=session)

    def _confirm(self, rec, token, project=None, session="", actor="user-1"):
        return ri.confirm_execution(rec["request_id"], token, actor,
                                    "execute-bounded-plan",
                                    project or self.project,
                                    rec["intent"]["goal"], rec["plan"]["steps"],
                                    session_id=session)

    # --------------------------------------------------- 1. session binding
    def test_bound_proposal_missing_session_rejected(self):
        rec = self._low("bind-missing")
        prop = self._propose(rec, session=self.VALID)
        with self.assertRaisesRegex(ri.RequestError,
                                    "session-binding-required"):
            self._confirm(rec, prop["confirm_token"], session="")

    def test_bound_proposal_different_session_rejected(self):
        rec = self._low("bind-different")
        prop = self._propose(rec, session=self.VALID)
        with self.assertRaisesRegex(ri.RequestError,
                                    "session-binding-required"):
            self._confirm(rec, prop["confirm_token"], session="another-session")

    def test_bound_proposal_invalid_session_rejected(self):
        rec = self._low("bind-invalid")
        prop = self._propose(rec, session=self.VALID)
        # Exact session ID but the panel authority reports it invalid/expired.
        with mock.patch.object(panelauth, "session_token_valid",
                               return_value=False):
            with self.assertRaisesRegex(ri.RequestError, "session-invalid"):
                self._confirm(rec, prop["confirm_token"], session=self.VALID)

    def test_bound_proposal_exact_valid_session_accepted_once(self):
        rec = self._low("bind-valid")
        prop = self._propose(rec, session=self.VALID)
        granted = self._confirm(rec, prop["confirm_token"], session=self.VALID)
        self.assertTrue(granted["consent_confirmed"])
        self.assertTrue(granted["actor_trusted"])
        first = ri.consume_execution(rec["request_id"], self.project, self.goal,
                                     rec["plan"], session_id=self.VALID)
        self.assertTrue(first["allowed"])
        second = ri.consume_execution(rec["request_id"], self.project, self.goal,
                                      rec["plan"], session_id=self.VALID)
        self.assertFalse(second["allowed"])

    def test_consumption_bound_session_cannot_be_bypassed(self):
        rec = self._low("bind-consume")
        prop = self._propose(rec, session=self.VALID)
        self._confirm(rec, prop["confirm_token"], session=self.VALID)
        self.assertEqual(ri.can_execute(rec["request_id"], project=self.project,
                                        goal=self.goal, plan=rec["plan"],
                                        session_id="")["reason"],
                         "execution-session-required")
        self.assertEqual(ri.consume_execution(rec["request_id"], self.project,
                                              self.goal, rec["plan"],
                                              session_id="other")["reason"],
                         "execution-session-mismatch")

    def test_anonymous_proposal_cannot_upgrade_identity(self):
        rec = self._low("anon-1")
        prop = self._propose(rec)  # genuinely no session
        granted = self._confirm(rec, prop["confirm_token"], session=self.VALID)
        self.assertTrue(granted["consent_confirmed"])
        self.assertFalse(granted["actor_trusted"])
        self.assertEqual(granted["identity_mode"], "self-declared")
        # Anonymous execution stays permitted but never becomes authenticated.
        res = ri.consume_execution(rec["request_id"], self.project, self.goal,
                                   rec["plan"], session_id=self.VALID)
        self.assertTrue(res["allowed"])
        self.assertEqual(ri.get(rec["request_id"])["execution"]["actor_trusted"],
                         False)

    # ----------------------------------------------- 2. project ownership
    def test_wrong_project_at_proposal_rejected(self):
        rec = self._low("proj-propose")
        with self.assertRaisesRegex(ri.RequestError,
                                    "project-ownership-mismatch"):
            self._propose(rec, project="other-project")

    def test_wrong_project_at_confirmation_rejected(self):
        rec = self._low("proj-confirm")
        prop = self._propose(rec)
        with self.assertRaisesRegex(ri.RequestError,
                                    "project-ownership-mismatch"):
            self._confirm(rec, prop["confirm_token"], project="other-project")

    def test_wrong_project_at_consumption_rejected(self):
        rec = self._low("proj-consume")
        prop = self._propose(rec)
        self._confirm(rec, prop["confirm_token"])
        res = ri.consume_execution(rec["request_id"], "other-project",
                                   self.goal, rec["plan"])
        self.assertFalse(res["allowed"])
        self.assertEqual(res["reason"], "execution-project-ownership-mismatch")

    def test_correct_project_through_all_steps_consumes_once(self):
        rec = self._low("proj-ok")
        prop = self._propose(rec)
        self._confirm(rec, prop["confirm_token"])
        self.assertTrue(ri.consume_execution(rec["request_id"], self.project,
                                             self.goal, rec["plan"])["allowed"])
        self.assertFalse(ri.consume_execution(rec["request_id"], self.project,
                                              self.goal, rec["plan"])["allowed"])

    # -------------------------------------- 3. consent vs authenticated actor
    def test_token_without_session_consent_only(self):
        rec = self._low("trust-1")
        prop = self._propose(rec)
        granted = self._confirm(rec, prop["confirm_token"])
        self.assertTrue(granted["consent_confirmed"])
        self.assertFalse(granted["actor_trusted"])

    def test_fabricated_actor_never_trusted_without_session(self):
        rec = self._low("trust-2")
        prop = self._propose(rec)
        granted = self._confirm(rec, prop["confirm_token"],
                                actor="totally-fabricated")
        self.assertFalse(granted["actor_trusted"])
        self.assertEqual(granted["identity_mode"], "self-declared")

    def test_high_critical_still_requires_approval(self):
        rec = self._high("trust-high")
        with self.assertRaisesRegex(ri.RequestError,
                                    "explicit-approval-required-for-execution"):
            self._propose(rec)
        prop = ri.approval_proposal(rec["request_id"])
        ri.approve(rec["request_id"], prop["confirm_token"], "user-1",
                   project=self.project)
        granted_prop = self._propose(rec)
        granted = self._confirm(rec, granted_prop["confirm_token"])
        self.assertTrue(granted["consent_confirmed"])
        self.assertFalse(granted["actor_trusted"])

    def test_api_view_exposes_no_token_or_session(self):
        rec = self._low("view-1")
        prop = self._propose(rec, session=self.VALID)
        self._confirm(rec, prop["confirm_token"], session=self.VALID)
        view = ri.save_view(ri.get(rec["request_id"]))
        blob = json.dumps(view, ensure_ascii=False)
        self.assertNotIn("token", blob)
        self.assertNotIn(self.VALID, blob)
        self.assertNotIn("session_id", blob)
        self.assertTrue(view["execution"]["consent_confirmed"])
        self.assertTrue(view["execution"]["actor_trusted"])


if __name__ == "__main__":
    unittest.main()
