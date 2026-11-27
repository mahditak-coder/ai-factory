"""Phase 02 critical corrections — independent-review defect regressions.

Real filesystem stores, Barrier-synchronised threads and mocked execution
adapters. No timing sleeps, no providers, no specialist execution.
"""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import (confirmations, factory, opstatus, orchestrator,
                  request_intelligence as ri, tasks)


class Phase02CorrectionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = self.base / "canonical_requests.json"
        self.store.write_text(json.dumps({"schema": ri.STORE_VERSION,
                                          "items": {}}), encoding="utf-8")
        self.flags = self.base / "flags.json"
        self.conf = self.base / "confirmations.json"
        self.conf.write_text(json.dumps({"items": {}}), encoding="utf-8")
        self.patches = [
            mock.patch.object(ri, "_STORE", self.store),
            mock.patch.object(ri, "_FLAGS", self.flags),
            mock.patch.object(confirmations, "_STORE", self.conf),
        ]
        for p in self.patches:
            p.start()
        d = config.PROJECTS_DIR / "p2-fix"
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text('{"name":"p2-fix","type":"webapp"}',
                                     encoding="utf-8")
        self.project = "p2-fix"

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _intake(self, text, rid=""):
        return ri.intake(text, project=self.project, request_id=rid)

    # ----------------------------------------------- 1. answers mutate model
    def test_answer_platform_android_changes_canonical_platform(self):
        rec = self._intake("...")
        out = ri.answer(rec["request_id"], "اندروید", "platform")
        self.assertEqual(out["classification"]["platform"]["value"], "Android")
        self.assertTrue(out["clarification"]["answers"][0]["resolved"])
        self.assertEqual(out["clarification"]["answers"][0]["value"], "Android")

    def test_answer_platform_web_changes_canonical_platform(self):
        rec = self._intake("...")
        out = ri.answer(rec["request_id"], "Web", "platform")
        self.assertEqual(out["classification"]["platform"]["value"], "Web")
        self.assertNotIn("platform", [m["id"] for m in
                                      out["clarification"]["missing"]])

    def test_answer_domain_game_changes_canonical_domain(self):
        rec = self._intake("...")
        out = ri.answer(rec["request_id"], "بازی", "domain")
        self.assertEqual(out["classification"]["domain"]["value"], "game")
        self.assertTrue(any(r["source"] == "inferred" and
                            ("game" in r["description"] or "بازی" in r["description"])
                            for r in out["requirements"]))

    def test_answer_intent_create_changes_canonical_intent(self):
        rec = self._intake("...")
        out = ri.answer(rec["request_id"], "ساخت", "intent")
        self.assertEqual(out["intent"]["primary"], "create")
        self.assertFalse(out["classification"]["intent"]["uncertain"])

    def test_invalid_answers_do_not_clear_essential_clarification(self):
        rec = self._intake("...")
        out = ri.answer(rec["request_id"], "نمیدانم", "platform")
        self.assertFalse(out["clarification"]["answers"][0]["resolved"])
        self.assertIn("platform", [m["id"] for m in
                                   out["clarification"]["missing"]])
        self.assertFalse(out["clarification"]["can_proceed"])
        self.assertEqual(out["status"], "NEEDS_CLARIFICATION")
        with self.assertRaises(ri.RequestError):
            ri.answer(rec["request_id"], "", "platform")

    def test_request_hash_reflects_complete_revised_state(self):
        rec = self._intake("...")
        out = ri.answer(rec["request_id"], "اندروید", "platform")
        self.assertNotEqual(out["request_hash"], rec["request_hash"])
        self.assertEqual(out["request_hash"], ri.request_hash(out))
        self.assertEqual(out["clarification"]["answers"][0]["revision"],
                         out["revision"])

    def test_answers_traceable_after_reload(self):
        rec = self._intake("...")
        ri.answer(rec["request_id"], "Android", "platform")
        reloaded = ri.get(rec["request_id"])
        entry = reloaded["clarification"]["answers"][0]
        self.assertEqual(entry["question_id"], "platform")
        self.assertEqual(entry["provenance"], "user-clarification")
        self.assertTrue(entry["at"] and entry["revision"])
        self.assertEqual(reloaded["raw_text"], rec["raw_text"])

    def test_material_answer_invalidates_stale_plan_and_approval(self):
        rec = ri.intake("ساخت سیستم پرداخت وب", project=self.project)
        rec = ri.build_plan(rec["request_id"])
        proposal = ri.approval_proposal(rec["request_id"])
        out = ri.answer(rec["request_id"], "اندروید", "platform")
        self.assertTrue(out["plan"].get("invalidated"))
        self.assertTrue(out["approval"]["invalidated"])
        with self.assertRaises(ri.RequestError):
            ri.approve(rec["request_id"], proposal["confirm_token"], "user")

    # ------------------------------------------- 2. no automatic approval
    def test_low_risk_planning_never_approves(self):
        rec = ri.intake("ساخت صفحه وب ساده", project=self.project)
        planned = ri.build_plan(rec["request_id"])
        self.assertEqual(planned["status"], "PLANNED")
        self.assertEqual(planned["approval"]["status"], "NOT_REQUIRED")
        self.assertFalse(planned["approval"].get("actor"))
        self.assertFalse(planned["approval"].get("actor_trusted"))
        check = ri.can_execute(rec["request_id"])
        self.assertFalse(check["allowed"])
        self.assertEqual(check["reason"], "no-execution-authorization")

    def test_no_fabricated_human_approval_in_history_or_view(self):
        rec = ri.intake("ساخت صفحه وب ساده", project=self.project)
        planned = ri.build_plan(rec["request_id"])
        self.assertNotIn("APPROVED", {h["to"] for h in planned["history"]})
        view = ri.save_view(planned)
        self.assertEqual(view["approval"]["status"], "NOT_REQUIRED")
        self.assertNotIn("token", view["approval"])

    # -------------------------------------------- 3. real execution boundary
    def _task(self):
        return tasks.add_task(self.project, "ساخت صفحه وب", agent="builder")

    def gate(self, goal="ساخت صفحه وب", task_id="t-correction"):
        return orchestrator.canonical_execution_gate(task_id, self.project, goal)

    def test_intake_failure_prevents_model_execution(self):
        fake_agent = {"id": "builder", "enabled": True, "allowed_types": None}
        with mock.patch("core.agents.get_agent", return_value=fake_agent):
            task = self._task()
            with mock.patch.object(ri, "intake",
                                   side_effect=ri.RequestStoreError("corrupt")):
                with mock.patch.object(factory, "generate",
                                       side_effect=AssertionError("model executed")):
                    orchestrator._execute(task, "worker-1")
        with mock.patch.object(ri, "intake",
                               side_effect=ri.RequestStoreError("corrupt")):
            blocked = self.gate()
        self.assertFalse(blocked["allowed"])
        self.assertIn("canonical-intake-failed", blocked["reason"])
        self.assertEqual(tasks.get_task(task["id"])["state"], "failed")

    def test_corrupt_or_missing_store_prevents_execution(self):
        self.store.write_text("{broken", encoding="utf-8")
        self.assertFalse(self.gate()["allowed"])
        Path(str(self.store) + ".initialized").write_text(ri.STORE_VERSION)
        self.store.unlink()
        blocked = self.gate()
        self.assertFalse(blocked["allowed"])

    def test_non_ready_statuses_prevent_execution(self):
        cases = [("...", "t-clar", "status:NEEDS_CLARIFICATION"),
                 ("حذف کن wszystko rm -rf", "t-block", "status:BLOCKED")]
        for text, tid, expected in cases:
            with self.subTest(text=text):
                g = self.gate(text, task_id=tid)
                self.assertFalse(g["allowed"])
                self.assertEqual(g["reason"], expected)
                self.assertFalse(ri.can_execute(g["request_id"])["allowed"])
        # A high-risk request is blocked even after a plan is generated:
        # planning is never permission to execute.
        rec = ri.intake("ساخت سیستم پرداخت وب", project=self.project,
                        request_id="req-task-t-approval")
        ri.build_plan(rec["request_id"])
        g = self.gate("ساخت سیستم پرداخت وب", task_id="t-approval")
        self.assertFalse(g["allowed"])
        self.assertEqual(g["reason"], "status:AWAITING_APPROVAL")

    def test_valid_exact_one_shot_approval_allows_bounded_action(self):
        rec = ri.intake("ساخت سیستم پرداخت وب", project=self.project)
        rec = ri.build_plan(rec["request_id"])
        proposal = ri.approval_proposal(rec["request_id"])
        approved = ri.approve(rec["request_id"], proposal["confirm_token"],
                              "user-1", project=self.project)
        self.assertEqual(approved["status"], "APPROVED")
        auth = ri.can_execute(rec["request_id"], project=self.project,
                              goal="ساخت سیستم پرداخت وب", plan=approved["plan"])
        self.assertFalse(auth["allowed"])
        self.assertEqual(auth["reason"], "no-execution-authorization")
        prop = ri.propose_execution(rec["request_id"], "user-1",
                                    "execute-bounded-plan", self.project,
                                    "ساخت سیستم پرداخت وب",
                                    approved["plan"]["steps"])
        ri.confirm_execution(rec["request_id"], prop["confirm_token"], "user-1",
                             "execute-bounded-plan", self.project,
                             "ساخت سیستم پرداخت وب", approved["plan"]["steps"])
        auth = ri.can_execute(rec["request_id"], project=self.project,
                              goal="ساخت سیستم پرداخت وب", plan=approved["plan"])
        self.assertTrue(auth["allowed"])
        self.assertEqual(auth["scope"], "execute-bounded-plan")
        self.assertEqual(auth["actor"], "user-1")

    def test_replayed_stale_mismatched_expired_wrong_project_approvals_fail(self):
        rec = ri.intake("ساخت سیستم پرداخت وب", project=self.project)
        rec = ri.build_plan(rec["request_id"])
        proposal = ri.approval_proposal(rec["request_id"])
        token = proposal["confirm_token"]
        # mismatched project
        with self.assertRaises(ri.RequestError):
            ri.approve(rec["request_id"], token, "user", project="other")
        # wrong token
        with self.assertRaises(ri.RequestError):
            ri.approve(rec["request_id"], "bogus", "user")
        # stale request after a material answer
        stale = ri.intake("ساخت سیستم پرداخت اندروید", project=self.project,
                          request_id=rec["request_id"] + "-stale")
        stale = ri.build_plan(stale["request_id"])
        sp = ri.approval_proposal(stale["request_id"])
        ri.answer(stale["request_id"], "Web", "platform")
        with self.assertRaises(ri.RequestError):
            ri.approve(stale["request_id"], sp["confirm_token"], "user")
        # expiry from authoritative store
        d = json.loads(self.conf.read_text(encoding="utf-8"))
        d["items"][token]["expires"] = 1
        self.conf.write_text(json.dumps(d), encoding="utf-8")
        with self.assertRaises(ri.RequestError):
            ri.approve(rec["request_id"], token, "user")
        # valid once, then replay
        d = json.loads(self.conf.read_text(encoding="utf-8"))
        d["items"][token]["expires"] = 9999999999
        self.conf.write_text(json.dumps(d), encoding="utf-8")
        self.assertEqual(ri.approve(rec["request_id"], token, "user")["status"],
                         "APPROVED")
        with self.assertRaises(ri.RequestError):
            ri.approve(rec["request_id"], token, "user")

    def test_legacy_mode_is_explicit_and_does_not_activate_on_error(self):
        self.assertEqual(ri.set_compat_mode("canonical")["mode"], "canonical")
        with mock.patch.object(ri, "intake",
                               side_effect=ri.RequestStoreError("corrupt")):
            self.assertFalse(self.gate()["allowed"])
        ri.set_compat_mode("legacy")
        g = self.gate()
        self.assertTrue(g["allowed"])
        self.assertEqual(g["mode"], "legacy")
        ri.set_compat_mode("canonical")

    # ------------------------------------------------ 4. concurrency/approval
    def test_concurrent_approval_one_winner(self):
        rec = ri.intake("ساخت سیستم پرداخت وب", project=self.project)
        ri.build_plan(rec["request_id"])
        proposal = ri.approval_proposal(rec["request_id"])
        token = proposal["confirm_token"]
        barrier = threading.Barrier(2)
        outcomes = []

        def worker():
            barrier.wait(timeout=10)
            try:
                ri.approve(rec["request_id"], token, "user", project=self.project)
                outcomes.append("ok")
            except ri.RequestError as exc:
                outcomes.append("err:" + str(exc))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        self.assertEqual(outcomes.count("ok"), 1)
        self.assertEqual(len(outcomes), 2)

    def test_concurrent_answer_and_approval_cannot_authorise_stale_request(self):
        rec = ri.intake("ساخت سیستم پرداخت وب", project=self.project)
        ri.build_plan(rec["request_id"])
        proposal = ri.approval_proposal(rec["request_id"])
        barrier = threading.Barrier(2)
        outcomes = []

        def approve_worker():
            barrier.wait(timeout=10)
            try:
                ri.approve(rec["request_id"], proposal["confirm_token"], "user")
                outcomes.append("approve-ok")
            except ri.RequestError as exc:
                outcomes.append("approve-err:" + str(exc))

        def answer_worker():
            barrier.wait(timeout=10)
            try:
                ri.answer(rec["request_id"], "Web", "platform")
                outcomes.append("answer-ok")
            except ri.RequestError as exc:
                outcomes.append("answer-err:" + str(exc))

        threads = [threading.Thread(target=approve_worker),
                   threading.Thread(target=answer_worker)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        self.assertEqual(len(outcomes), 2)
        if "approve-ok" in outcomes:
            self.assertIn("answer-err", outcomes[outcomes.index("approve-ok") - 1]
                          if outcomes.index("approve-ok") > 0 else
                          outcomes[outcomes.index("approve-ok") + 1])
        else:
            self.assertTrue(any("approve-err" in o for o in outcomes))

    def test_token_never_exposed_in_api_view_or_workspace_payload(self):
        rec = ri.intake("ساخت سیستم پرداخت وب", project=self.project)
        ri.build_plan(rec["request_id"])
        ri.approval_proposal(rec["request_id"])
        token = ri.get(rec["request_id"])["approval"]["token"]
        self.assertTrue(token)
        view = ri.save_view(ri.get(rec["request_id"]))
        self.assertNotIn("token", view["approval"])
        self.assertNotIn(token, json.dumps(view, ensure_ascii=False))

    # --------------------------------------------------- 5. risk classifier
    def test_all_nine_risk_dimensions_honest(self):
        cases = [
            ("انتشار عمومی نسخه را انجام بده", "external_release", "high"),
            ("publish the app publicly", "external_release", "high"),
            ("استقرار سرویس را انجام بده", "external_release", "high"),
            ("سیستم پرداخت آنلاین بساز", "financial", "high"),
            ("use my api key for the service", "credentials", "critical"),
            ("save personal data of patients", "private_data", "high"),
            ("call http://example.com endpoint", "network", "medium"),
            ("rm -rf the build folder", "destructive", "critical"),
            ("مهاجرت دیتابیس را انجام بده", "data_loss_migration", "high"),
        ]
        for text, dimension, expected in cases:
            with self.subTest(text=text):
                risk = ri.classify_risk(text)
                self.assertTrue(risk["dimensions"][dimension]["present"], text)
                self.assertEqual(risk["level"], expected, text)

    def test_unknown_high_impact_is_not_low(self):
        for text in ("یک عملیات خارجی نامشخص", "کاری ناشناخته انجام بده"):
            with self.subTest(text=text):
                self.assertNotEqual(ri.classify_risk(text)["level"], "low")


if __name__ == "__main__":
    unittest.main()
