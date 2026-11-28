"""Phase 04 consolidated final — issuer closure, fences and QA lease races."""
import hashlib
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import (agent_collaboration as ac, confirmations, opstatus, paths,
                  specialist_engine as se, tool_events, tool_executor,
                  tool_identity, tool_registry)
from core.tests import specialist_run_token

VALID_SESSION = "valid-panel-session"


class FinalSecurityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.canon = self.base / "canon.json"
        self.legacy = self.base / "legacy.json"
        self.legacy.write_text("[]", encoding="utf-8")
        self.flags = self.base / "flags.json"
        self.conf = self.base / "confirmations.json"
        self.ev = self.base / "events.json"
        self.rc = self.base / "receipts.json"
        self.ev.write_text("[]", encoding="utf-8")
        self.rc.write_text("{}", encoding="utf-8")
        self.patches = [
            mock.patch.object(se, "_STORE", self.canon),
            mock.patch.object(se, "_LEGACY_STORE", self.legacy),
            mock.patch.object(se, "_FLAGS", self.flags),
            mock.patch.object(ac, "_STORE", self.legacy),
            mock.patch.object(confirmations, "_STORE", self.conf),
            mock.patch.object(tool_events, "_STORE", self.ev),
            mock.patch.object(tool_events, "_RECEIPT_STORE", self.rc),
            mock.patch("core.panelauth.principal_for_session",
                       side_effect=lambda t: ("panel_admin"
                                              if t == VALID_SESSION else None)),
        ]
        for p in self.patches:
            p.start()
        se.set_engine_mode("canonical")
        self.project = "p4-final"
        d = config.PROJECTS_DIR / self.project
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text('{"name":"p4-final","type":"webapp"}',
                                     encoding="utf-8")
        (d / "sample.txt").write_text("hello", encoding="utf-8")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _cap(self, tool="project.read_file", scope="project:read", project=None):
        return tool_identity.issue_service_capability(
            "infrastructure", project or self.project, tool, scope,
            session_id=VALID_SESSION)

    def _tool(self, tid, scope="project:read"):
        calls = []
        tool_registry.registry().register(tool_registry.ToolSpec(
            tid, "P4", "test", "test", "test", scope, {},
            timeout_s=10, adapter=lambda p, i, **k: calls.append(1) or {"ok": 1}))
        return calls

    def _seed_developer(self):
        text = "def main(): pass\n"
        fp = paths.project_path(self.project, "src/game.py")
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(text, encoding="utf-8")
        tok = "seed-dev"
        se.start_run(self.project, "developer", tok)
        se.record_result(self.project, "developer", tok, opstatus.PASS,
                         [{"path": "src/game.py",
                           "sha256": hashlib.sha256(text.encode()).hexdigest()}])

    # ------------------------------------------- issuer closure
    def test_01_no_public_issuer_getter(self):
        self.assertFalse(hasattr(tool_identity, "server_issuer_key"))
        self.assertFalse(hasattr(tool_identity, "_ISSUER_KEY"))
        view = tool_identity.registered_services()
        self.assertTrue(all(isinstance(v, list) for v in view.values()))

    def test_02_imported_caller_cannot_mint(self):
        with self.assertRaises(tool_identity.IdentityError):
            tool_identity.issue_service_capability(
                "infrastructure", self.project, "project.read_file",
                "project:read")

    def test_03_no_identity_rejected_before_adapter(self):
        tid = "p4-final-none"
        calls = self._tool(tid)
        req = tool_executor.ToolRequest(
            tool_id=tid, project=self.project, inputs={}, scopes=["project:read"],
            call_id="p4-final-none")
        self.assertEqual(tool_executor.execute(req).status, opstatus.BLOCKED)
        self.assertEqual(calls, [])

    def test_04_forged_capability_rejected_before_adapter(self):
        tid = "p4-final-forged"
        calls = self._tool(tid)
        req = tool_executor.ToolRequest(
            tool_id=tid, project=self.project, inputs={}, scopes=["project:read"],
            capability_token="forged", call_id="p4-final-forged")
        self.assertEqual(tool_executor.execute(req).status, opstatus.BLOCKED)
        self.assertEqual(calls, [])

    def test_05_valid_service_scoped(self):
        req = tool_executor.ToolRequest(
            tool_id="project.read_file", project=self.project,
            inputs={"path": "sample.txt"}, scopes=["project:read"],
            capability_token=self._cap(), call_id="p4-final-cap")
        self.assertEqual(tool_executor.execute(req).status, opstatus.PASS)
        wrong = tool_executor.ToolRequest(
            tool_id="project.write_file", project=self.project,
            inputs={"path": "x.txt", "content": "x"}, scopes=["project:write"],
            capability_token=self._cap(), call_id="p4-final-cap-wrong")
        self.assertEqual(tool_executor.execute(wrong).status, opstatus.BLOCKED)

    def test_06_role_authorization_still_enforced(self):
        ok = tool_executor.ToolRequest(
            tool_id="project.read_file", project=self.project,
            inputs={"path": "sample.txt"}, scopes=["project:read"],
            role_id="developer", enforce_role=True,
            run_token=specialist_run_token(self.project, "developer"), call_id="p4-final-role")
        self.assertEqual(tool_executor.execute(ok).status, opstatus.PASS)
        denied = tool_executor.ToolRequest(
            tool_id="project.write_file", project=self.project,
            inputs={"path": "x.txt", "content": "x"}, scopes=["project:write"],
            role_id="product-architect", enforce_role=True,
            call_id="p4-final-role-deny")
        self.assertEqual(tool_executor.execute(denied).status, opstatus.BLOCKED)

    # --------------------------------------------- QA lease fencing
    def _qa_tool(self, n):
        calls = []

        def adapter(project, inputs, **kw):
            calls.append(kw.get("call_id"))
            if n is not None and len(calls) == n:
                se.start_run(project, "qa-engineer", "supersede-%d" % n,
                             supersede=True)
            return {"status": opstatus.PASS, "ok": True, "output": {},
                    "error": "", "call_id": kw.get("call_id", "")}
        return adapter

    def test_07_qa_superseded_during_tests_run(self):
        self._seed_developer()
        seen = []

        def fake(req):
            seen.append(req.tool_id)
            res = mock.Mock(status=opstatus.PASS, error="", call_id=req.call_id)
            if len(seen) == 1:
                se.start_run(self.project, "qa-engineer", "sup-1",
                             supersede=True)
            return res

        with mock.patch.object(ac, "execute_tool", side_effect=fake):
            result = ac.independent_qa(self.project)
        self.assertEqual(result["status"], opstatus.BLOCKED)
        self.assertFalse((paths.project_path(self.project,
                                            "spec/test-report.md").exists()))

    def test_08_qa_superseded_during_security_scan(self):
        self._seed_developer()
        seen = []

        def fake(req):
            seen.append(req.tool_id)
            res = mock.Mock(status=opstatus.PASS, error="", call_id=req.call_id)
            if len(seen) == 2:
                se.start_run(self.project, "qa-engineer", "sup-2",
                             supersede=True)
            return res

        with mock.patch.object(ac, "execute_tool", side_effect=fake):
            result = ac.independent_qa(self.project)
        self.assertEqual(result["status"], opstatus.BLOCKED)
        self.assertFalse((paths.project_path(self.project,
                                            "spec/test-report.md").exists()))

    def test_09_qa_superseded_before_write(self):
        self._seed_developer()
        ok = mock.Mock(status=opstatus.PASS, error="", call_id="x")
        with mock.patch.object(ac, "execute_tool", return_value=ok), \
             mock.patch.object(ac, "write_artifact",
                               side_effect=lambda *a, **k: (
                                   se.start_run(self.project, "qa-engineer",
                                                "sup-3", supersede=True) or
                                   {"status": opstatus.PASS,
                                    "sha256": "f" * 64})):
            result = ac.independent_qa(self.project)
        self.assertEqual(result["status"], opstatus.BLOCKED)

    def test_10_qa_failed_write_not_pass(self):
        self._seed_developer()
        ok = mock.Mock(status=opstatus.PASS, error="", call_id="x")
        with mock.patch.object(ac, "execute_tool", return_value=ok), \
             mock.patch.object(ac, "write_artifact",
                               return_value={"status": opstatus.BLOCKED,
                                             "error": "denied", "sha256": ""}):
            result = ac.independent_qa(self.project)
        self.assertEqual(result["status"], opstatus.FAIL)

    def test_11_qa_hash_mismatch_not_pass(self):
        self._seed_developer()
        ok = mock.Mock(status=opstatus.PASS, error="", call_id="x")
        def fake_write(*a, **k):
            fp = paths.project_path(self.project, "spec/test-report.md")
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text("different content", encoding="utf-8")
            return {"status": opstatus.PASS, "sha256": "f" * 64}
        with mock.patch.object(ac, "execute_tool", return_value=ok), \
             mock.patch.object(ac, "write_artifact", side_effect=fake_write):
            result = ac.independent_qa(self.project)
        self.assertEqual(result["status"], opstatus.FAIL)
        self.assertTrue(any("hash-mismatch" in p for p in result["problems"]))

    def test_12_qa_terminal_failure_not_pass(self):
        self._seed_developer()
        ok = mock.Mock(status=opstatus.PASS, error="", call_id="x")
        with mock.patch.object(ac, "execute_tool", return_value=ok), \
             mock.patch.object(se, "record_result",
                               return_value={"status": opstatus.BLOCKED}):
            result = ac.independent_qa(self.project)
        self.assertEqual(result["status"], opstatus.BLOCKED)

    def test_13_concurrent_qa_one_pass(self):
        self._seed_developer()
        barrier = threading.Barrier(2)
        out, lock = [], threading.Lock()

        def work():
            barrier.wait(timeout=10)
            res = ac.independent_qa(self.project)
            with lock:
                out.append(res["status"])

        threads = [threading.Thread(target=work) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(len(out), 2)
        self.assertLessEqual(out.count(opstatus.PASS), 1)

    def test_14_phase01_and_phase02_regressions(self):
        # Phase 02 request lifecycle still works.
        from core import request_intelligence as ri
        d = config.PROJECTS_DIR / "p4-req"
        d.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(ri, "_STORE", self.base / "req.json"), \
             mock.patch.object(ri, "_FLAGS", self.base / "flags2.json"):
            rec = ri.intake("ساخت صفحه وب", project="p4-req", request_id="r1")
            self.assertEqual(ri.validate_record(rec), [])
        # Phase 01 receipt lifecycle module still exposes the atomic claim.
        from core import tool_events
        self.assertTrue(callable(getattr(tool_events, "claim", None)))


if __name__ == "__main__":
    unittest.main()
