"""Phase 04 final — mandatory ToolExecutor identity and QA lease lifecycle."""
import hashlib
import time
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import (agent_collaboration as ac, confirmations, opstatus, paths,
                  specialist_engine as se, tool_events, tool_executor,
                  tool_identity)


class IdentityEnforcementTest(unittest.TestCase):
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
        ]
        for p in self.patches:
            p.start()
        se.set_engine_mode("canonical")
        self.project = "p4-ident"
        self.other = "p4-other"
        for name in (self.project, self.other):
            d = config.PROJECTS_DIR / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "meta.json").write_text(
                '{"name":"%s","type":"webapp"}' % name, encoding="utf-8")
        (config.PROJECTS_DIR / self.project / "sample.txt").write_text(
            "hello", encoding="utf-8")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _cap(self, project=None, tool="project.read_file", scope="project:read",
             ttl=300):
        return tool_identity.issue_infrastructure_capability(
            project or self.project, tool, scope,
            issuer_key=tool_identity.server_issuer_key(), ttl_s=ttl)

    def _req(self, tool="project.read_file", project=None, inputs=None,
             scopes=None, **extra):
        return tool_executor.ToolRequest(
            tool_id=tool, project=project or self.project,
            inputs=inputs if inputs is not None else {"path": "sample.txt"},
            scopes=scopes or ["project:read"], **extra)

    # -------------------------------------------- mandatory identity
    def test_01_no_identity_blocked(self):
        result = tool_executor.execute(self._req(call_id="id-none"))
        self.assertEqual(result.status, opstatus.BLOCKED)
        self.assertIn("identity-required", result.error)

    def test_02_forged_capability_blocked(self):
        result = tool_executor.execute(self._req(
            capability_token="forged-token", call_id="id-forged"))
        self.assertEqual(result.status, opstatus.BLOCKED)
        self.assertIn("capability-invalid", result.error)

    def test_03_expired_capability_blocked(self):
        token = self._cap(ttl=1)
        with mock.patch("core.tool_identity.time.time",
                        return_value=time.time() + 10):
            result = tool_executor.execute(self._req(
                capability_token=token, call_id="id-expired"))
        self.assertEqual(result.status, opstatus.BLOCKED)

    def test_04_wrong_scope_capability_blocked(self):
        token = self._cap(scope="project:read")
        result = tool_executor.execute(self._req(
            tool="project.write_file",
            inputs={"path": "x.txt", "content": "x"},
            scopes=["project:write"], capability_token=token,
            call_id="id-scope"))
        self.assertEqual(result.status, opstatus.BLOCKED)

    def test_05_wrong_project_capability_blocked(self):
        token = self._cap(project=self.other)
        result = tool_executor.execute(self._req(
            capability_token=token, call_id="id-project"))
        self.assertEqual(result.status, opstatus.BLOCKED)

    def test_06_caller_cannot_mint_capability(self):
        with self.assertRaises(tool_identity.IdentityError):
            tool_identity.issue_infrastructure_capability(
                self.project, "project.read_file", "project:read")

    def test_07_valid_role_within_contract(self):
        result = tool_executor.execute(self._req(
            role_id="developer", enforce_role=True, call_id="id-role-ok"))
        self.assertEqual(result.status, opstatus.PASS)

    def test_08_role_scope_denied(self):
        result = tool_executor.execute(self._req(
            tool="project.write_file",
            inputs={"path": "x.txt", "content": "x"},
            scopes=["project:write"], role_id="product-architect",
            enforce_role=True, call_id="id-role-deny"))
        self.assertEqual(result.status, opstatus.BLOCKED)
        self.assertIn("role-authorization-denied", result.error)

    def test_09_valid_capability_bounded(self):
        token = self._cap()
        self.assertEqual(tool_executor.execute(self._req(
            capability_token=token, call_id="id-cap-ok")).status, opstatus.PASS)
        # The capability is bounded to this project/tool/scope and expires;
        # it is not one-shot per call_id and remains valid for that grant.
        self.assertEqual(tool_executor.execute(self._req(
            capability_token=token, call_id="id-cap-reuse")).status,
            opstatus.PASS)

    def test_10_ambiguous_identity_blocked(self):
        result = tool_executor.execute(self._req(
            role_id="developer", enforce_role=True,
            capability_token=self._cap(), call_id="id-ambiguous"))
        self.assertEqual(result.status, opstatus.BLOCKED)
        self.assertIn("ambiguous-identity", result.error)

    # -------------------------------------------------- QA lifecycle
    def _seed_developer(self):
        text = "def main(): pass\n"
        fp = paths.project_path(self.project, "src/game.py")
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(text, encoding="utf-8")
        sha = hashlib.sha256(text.encode()).hexdigest()
        tok = "seed-qa-dep"
        se.start_run(self.project, "developer", tok)
        se.record_result(self.project, "developer", tok, opstatus.PASS,
                         [{"path": "src/game.py", "sha256": sha}])

    def test_11_qa_stale_run_blocked(self):
        self._seed_developer()
        stale = se.start_run(self.project, "qa-engineer", "qa-stale")
        se.start_run(self.project, "qa-engineer", "qa-current",
                     supersede=True)
        result = ac.independent_qa(self.project)
        self.assertEqual(result["status"], opstatus.BLOCKED)

    def test_12_qa_failed_tool_not_pass(self):
        self._seed_developer()
        failed = mock.Mock(status=opstatus.FAIL, error="tool-failed",
                           call_id="x")
        with mock.patch.object(ac, "execute_tool", return_value=failed):
            result = ac.independent_qa(self.project)
        self.assertEqual(result["status"], opstatus.FAIL)

    def test_13_qa_failed_artifact_write_not_pass(self):
        self._seed_developer()
        with mock.patch.object(ac, "write_artifact", return_value={
                "status": opstatus.BLOCKED, "error": "write-denied",
                "sha256": ""}):
            result = ac.independent_qa(self.project)
        self.assertEqual(result["status"], opstatus.FAIL)

    def test_14_qa_failed_terminal_not_pass(self):
        self._seed_developer()
        with mock.patch.object(se, "record_result",
                               return_value={"status": opstatus.BLOCKED}):
            result = ac.independent_qa(self.project)
        self.assertEqual(result["status"], opstatus.BLOCKED)

    def test_15_qa_current_run_pass(self):
        self._seed_developer()
        result = ac.independent_qa(self.project)
        self.assertEqual(result["status"], opstatus.PASS)


if __name__ == "__main__":
    unittest.main()
