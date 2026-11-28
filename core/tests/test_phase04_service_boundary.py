"""Adversarial tests for quality_gate/workflow_executor service delegation."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import (opstatus, quality_gate, tool_events, tool_identity,
                  tool_registry, workflow_executor)


class ServiceBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.ev = self.base / "events.json"
        self.rc = self.base / "receipts.json"
        self.ev.write_text("[]", encoding="utf-8")
        self.rc.write_text("{}", encoding="utf-8")
        self.patches = [mock.patch.object(tool_events, "_STORE", self.ev),
                        mock.patch.object(tool_events, "_RECEIPT_STORE", self.rc)]
        for p in self.patches:
            p.start()
        self.project = "p4-service"
        d = config.PROJECTS_DIR / self.project
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text('{"name":"p4-service","type":"webapp"}',
                                     encoding="utf-8")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_01_no_issuer_secret_exposed(self):
        self.assertFalse(hasattr(tool_identity, "server_issuer_key"))
        self.assertFalse(hasattr(tool_identity, "_ISSUER_KEY"))

    def test_02_quality_helper_rejects_unauthorized_tool(self):
        result = quality_gate._call(self.project, "image.generate",
                                    {"prompt": "x"}, ["image:generate"])
        self.assertEqual(result["status"], opstatus.BLOCKED)
        self.assertIn("service-operation-denied", result["error"])

    def test_03_workflow_helper_rejects_unauthorized_tool(self):
        result = workflow_executor._call(self.project, "image.generate",
                                         {"prompt": "x"}, ["image:generate"])
        self.assertEqual(result["status"], opstatus.BLOCKED)

    def test_04_wrong_scope_rejected(self):
        result = quality_gate._call(self.project, "project.write_file",
                                    {"path": "x.json", "content": "{}"},
                                    ["shell:execute"])
        self.assertEqual(result["status"], opstatus.BLOCKED)

    def test_05_legitimate_fixed_operation_works(self):
        result = quality_gate._call(
            self.project, "project.write_file",
            {"path": "quality/report.json", "content": "{}"}, ["project:write"])
        self.assertEqual(result["status"], opstatus.PASS)

    def test_06_imported_caller_cannot_expand_service(self):
        with self.assertRaises(tool_identity.IdentityError):
            tool_identity.issue_service_capability(
                "quality-gate", self.project, "image.generate",
                "image:generate")

    def test_07_exact_service_matrix_declared(self):
        matrix = tool_identity.registered_services()
        self.assertIn("quality-gate", matrix)
        self.assertIn("workflow-executor", matrix)
        self.assertNotIn("server:infrastructure", matrix)


if __name__ == "__main__":
    unittest.main()
