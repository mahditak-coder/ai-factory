"""Phase 02 final execution-boundary corrections — deterministic regressions.

No model/provider/specialist execution: execution adapters are mocked to fail
if touched. All canonical stores are isolated temporary files.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import (confirmations, factory, orchestrator,
                  request_intelligence as ri, tasks)


class ExecutionBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = self.base / "canonical_requests.json"
        self.store.write_text(json.dumps({"schema": ri.STORE_VERSION,
                                          "items": {}}), encoding="utf-8")
        self.flags = self.base / "flags.json"
        self.conf = self.base / "confirmations.json"
        self.conf.write_text(json.dumps({"items": {}}), encoding="utf-8")
        self.patches = [mock.patch.object(ri, "_STORE", self.store),
                        mock.patch.object(ri, "_FLAGS", self.flags),
                        mock.patch.object(confirmations, "_STORE", self.conf)]
        for p in self.patches:
            p.start()
        ri.set_compat_mode("canonical")
        d = config.PROJECTS_DIR / "p2-boundary"
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text('{"name":"p2-boundary","type":"webapp"}',
                                     encoding="utf-8")
        self.project = "p2-boundary"
        self.goal = "ساخت صفحه وب ساده"

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _low(self, rid="low-1", goal=None):
        goal = goal or self.goal
        rec = ri.intake(goal, project=self.project, request_id=rid)
        return ri.build_plan(rid)

    def _high(self, rid="high-1", goal="ساخت سیستم پرداخت وب"):
        rec = ri.intake(goal, project=self.project, request_id=rid)
        rec = ri.build_plan(rid)
        proposal = ri.approval_proposal(rid)
        ri.approve(rid, proposal["confirm_token"], "user-1", project=self.project)
        return ri.get(rid)

    def _authorize(self, rec, goal=None, steps=None, rid=None):
        goal = goal or rec.get("intent", {}).get("goal") or self.goal
        return ri.authorize_execution(rid or rec["request_id"], "user-1",
                                      "execute-bounded-plan", self.project,
                                      goal, steps if steps is not None
                                      else rec["plan"]["steps"])

    def _gate(self, task_id, goal=None, kind="generate"):
        return orchestrator.canonical_execution_gate(
            task_id, self.project, goal or self.goal, plan=None)

    # ------------------------------------------ 1. no implicit authorization
    def test_low_risk_intake_cannot_execute(self):
        rec = ri.intake(self.goal, project=self.project, request_id="low-intake")
        check = ri.can_execute(rec["request_id"], project=self.project,
                               goal=self.goal, plan=rec.get("plan"))
        self.assertFalse(check["allowed"])
        self.assertEqual(check["reason"], "no-execution-authorization")

    def test_low_risk_ready_for_plan_cannot_execute(self):
        rec = ri.intake("...", project=self.project, request_id="low-ready")
        rec = ri.answer(rec["request_id"], "Web", "platform")
        rec = ri.answer(rec["request_id"], "ساخت", "intent")
        rec = ri.answer(rec["request_id"], "کسب و کار", "domain")
        rec = ri.get(rec["request_id"])
        check = ri.can_execute(rec["request_id"], project=self.project,
                               goal=self.goal, plan=rec.get("plan"))
        self.assertFalse(check["allowed"])
        self.assertEqual(check["reason"], "no-execution-authorization")

    def test_low_risk_planned_not_required_cannot_execute(self):
        rec = self._low("low-planned")
        self.assertEqual(rec["approval"]["status"], "NOT_REQUIRED")
        check = ri.can_execute(rec["request_id"], project=self.project,
                               goal=self.goal, plan=rec["plan"])
        self.assertFalse(check["allowed"])
        self.assertEqual(check["reason"], "no-execution-authorization")

    def test_medium_risk_cannot_execute_without_authorization(self):
        rec = ri.intake("create api call http://example.com endpoint on web", project=self.project,
                        request_id="medium-1")
        rec = ri.build_plan(rec["request_id"])
        check = ri.can_execute(rec["request_id"], project=self.project,
                               goal="create api call http://example.com endpoint on web",
                               plan=rec["plan"])
        self.assertFalse(check["allowed"])
        self.assertIn(check["reason"],
                      ("status:AWAITING_APPROVAL", "no-execution-authorization"))

    def test_high_critical_blocked_until_approval_and_authorization(self):
        rec = ri.intake("ساخت سیستم پرداخت وب", project=self.project,
                        request_id="high-2")
        rec = ri.build_plan(rec["request_id"])
        blocked = ri.can_execute(rec["request_id"], project=self.project,
                                 goal="ساخت سیستم پرداخت وب", plan=rec["plan"])
        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["reason"], "status:AWAITING_APPROVAL")
        proposal = ri.approval_proposal(rec["request_id"])
        ri.approve(rec["request_id"], proposal["confirm_token"], "user",
                   project=self.project)
        still = ri.can_execute(rec["request_id"], project=self.project,
                               goal="ساخت سیستم پرداخت وب", plan=rec["plan"])
        self.assertFalse(still["allowed"])
        self.assertEqual(still["reason"], "no-execution-authorization")
        self._authorize(rec, goal="ساخت سیستم پرداخت وب", rid="high-2")
        ok = ri.can_execute(rec["request_id"], project=self.project,
                            goal="ساخت سیستم پرداخت وب", plan=rec["plan"])
        self.assertTrue(ok["allowed"])

    def test_plan_ready_is_never_execution_permission(self):
        rec = self._low("plan-ready")
        self.assertEqual(rec["status"], "PLANNED")
        with mock.patch.object(factory, "generate",
                               side_effect=AssertionError("model executed")):
            g = self._gate("t-plan-ready")
        self.assertFalse(g["allowed"])
        self.assertIn("no-execution-authorization", g["reason"])

    # ---------------------------------------- 2. exact bounded plan scope
    def test_approval_cannot_authorize_a_different_plan(self):
        rec = self._high("scope-plan")
        self._authorize(rec, rid="scope-plan")
        other = dict(rec["plan"])
        other["steps"] = list(rec["plan"]["steps"]) + [{
            "id": "extra", "purpose": "unapproved step"}]
        check = ri.can_execute(rec["request_id"], project=self.project,
                               goal="ساخت سیستم پرداخت وب", plan=other)
        self.assertFalse(check["allowed"])
        self.assertEqual(check["reason"], "execution-steps-mismatch")

    def test_approval_cannot_authorize_another_project(self):
        rec = self._low("scope-project")
        self._authorize(rec, rid="scope-project")
        check = ri.can_execute(rec["request_id"], project="other-project",
                               goal=self.goal, plan=rec["plan"])
        self.assertFalse(check["allowed"])
        self.assertEqual(check["reason"], "execution-project-mismatch")

    def test_approval_cannot_authorize_changed_goal(self):
        rec = self._low("scope-goal")
        self._authorize(rec, rid="scope-goal")
        check = ri.can_execute(rec["request_id"], project=self.project,
                               goal="ساخت بازی اندرویدی", plan=rec["plan"])
        self.assertFalse(check["allowed"])
        self.assertEqual(check["reason"], "execution-goal-mismatch")

    def test_extra_or_modified_steps_rejected(self):
        rec = self._low("scope-steps")
        self._authorize(rec, rid="scope-steps")
        modified = json.loads(json.dumps(rec["plan"]))
        modified["steps"][0]["purpose"] = "silently changed"
        self.assertFalse(ri.can_execute(rec["request_id"], project=self.project,
                                        goal=self.goal, plan=modified)["allowed"])
        extra = json.loads(json.dumps(rec["plan"]))
        extra["steps"].append({"id": "extra-step"})
        self.assertFalse(ri.can_execute(rec["request_id"], project=self.project,
                                        goal=self.goal, plan=extra)["allowed"])

    def test_valid_approval_does_not_authorize_unrestricted_legacy_execution(self):
        rec = self._high("scope-legacy")
        self._authorize(rec, rid="scope-legacy")
        check = ri.can_execute(rec["request_id"], project=self.project,
                               goal="ساخت سیستم پرداخت وب", plan=None)
        self.assertFalse(check["allowed"])
        self.assertEqual(check["reason"], "bounded-plan-required")
        with mock.patch.object(factory, "generate",
                               side_effect=AssertionError("legacy model ran")):
            g = self._gate("t-legacy", goal="ساخت سیستم پرداخت وب")
        self.assertFalse(g["allowed"])

    def test_unsupported_execution_adapter_fails_closed(self):
        rec = self._low("scope-adapter")
        with self.assertRaisesRegex(ri.RequestError, "scope-unsupported"):
            ri.authorize_execution(rec["request_id"], "user", "unrestricted",
                                   self.project, self.goal, rec["plan"]["steps"])
        check = ri.can_execute(rec["request_id"], project=self.project,
                               goal=self.goal, plan=rec["plan"],
                               scope="unrestricted")
        self.assertFalse(check["allowed"])

    # ------------------------------------------- 3. pipeline gate coverage
    def _pipeline_task(self, tid="t-pipe", goal=None):
        fake_agent = {"id": "builder", "enabled": True, "allowed_types": None}
        with mock.patch("core.agents.get_agent", return_value=fake_agent):
            return tasks.add_task(self.project, goal or self.goal,
                                  agent="builder", kind="pipeline")

    def _run_pipeline(self, task):
        with mock.patch("core.pipeline.execute",
                        side_effect=AssertionError("pipeline side effect")):
            orchestrator._execute(tasks.get_task(task["id"]), "w1")

    def test_pipeline_blocked_when_intake_fails(self):
        task = self._pipeline_task("t-pipe-intake")
        with mock.patch.object(ri, "intake",
                               side_effect=ri.RequestStoreError("corrupt")):
            self._run_pipeline(task)
        self.assertEqual(tasks.get_task(task["id"])["state"], "failed")

    def test_pipeline_blocked_on_clarification_and_approval_pending(self):
        clar_task = self._pipeline_task("t-pipe-clar", goal="...")
        self._run_pipeline(clar_task)
        self.assertEqual(tasks.get_task(clar_task["id"])["state"], "failed")
        high_task = self._pipeline_task("t-pipe-approval",
                                        goal="ساخت سیستم پرداخت وب")
        self._run_pipeline(high_task)
        self.assertEqual(tasks.get_task(high_task["id"])["state"], "failed")

    def test_pipeline_blocked_with_stale_authorization(self):
        rec = self._low("pipe-stale")
        self._authorize(rec, rid="pipe-stale")
        ri.answer(rec["request_id"], "اندروید", "platform")
        task = self._pipeline_task("t-pipe-stale")
        self._run_pipeline(task)
        self.assertEqual(tasks.get_task(task["id"])["state"], "failed")

    def test_no_pipeline_side_effect_before_gate(self):
        task = self._pipeline_task("t-pipe-none")
        with mock.patch("core.pipeline.execute") as pipe:
            orchestrator._execute(tasks.get_task(task["id"]), "w1")
        pipe.assert_not_called()
        self.assertFalse(self._gate("t-pipe-none")["allowed"])

    def test_ordinary_task_behavior_under_explicit_legacy_mode(self):
        ri.set_compat_mode("legacy")
        try:
            task = self._pipeline_task("t-legacy-ok")
            with mock.patch("core.pipeline.execute", return_value=None) as pipe:
                orchestrator._execute(tasks.get_task(task["id"]), "w1")
            pipe.assert_called_once()
        finally:
            ri.set_compat_mode("canonical")


if __name__ == "__main__":
    unittest.main()
