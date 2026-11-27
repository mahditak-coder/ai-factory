"""AF-42 — real Environment Capability Model and adaptive planning (UNIT)."""
import json
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import agent_planner, project_snapshot


def _make(name: str, node: bool = False) -> Path:
    d = config.PROJECTS_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps({"name": name, "type": "webpage"}),
                                 encoding="utf-8")
    if node:
        (d / "package.json").write_text(json.dumps(
            {"scripts": {"test": "node --test"}}), encoding="utf-8")
        (d / "tests").mkdir(exist_ok=True)
        (d / "tests/a.test.js").write_text(
            'const test=require("node:test");test("a",()=>{});', encoding="utf-8")
    else:
        (d / "server.py").write_text("def main(): return 1\n", encoding="utf-8")
        (d / "tests").mkdir(exist_ok=True)
        (d / "tests/test_a.py").write_text(
            "def test_a(): assert 1 == 1\ntest_a()\n", encoding="utf-8")
    return d


class EnvironmentModelTest(unittest.TestCase):
    def test_real_tools_and_policy_are_source_tagged(self):
        name = "af42-env"
        _make(name)
        env = project_snapshot.environment(name, refresh=True)
        self.assertEqual(env["status"], "verified")
        self.assertIn("python3", env["tools"])
        self.assertEqual(env["tools"]["python3"]["status"], "verified")
        self.assertIn("shutil.which", env["tools"]["python3"]["source"])
        caps = env["capabilities"]
        self.assertIn("test-python", caps)
        self.assertEqual(caps["network"]["status"], "verified")
        self.assertNotEqual(caps["network"]["value"], True)

    def test_missing_tool_is_honest_and_alternative_selected(self):
        name = "af42-missing"
        _make(name)
        real_which = project_snapshot.shutil.which
        with mock.patch.object(project_snapshot.shutil, "which",
                               side_effect=lambda n: None if n == "pytest"
                               else real_which(n)):
            env = project_snapshot.environment(name, refresh=True)
            cmd = project_snapshot.allowed_test_command(name, "python")
        self.assertFalse(env["capabilities"]["test-pytest"]["value"])
        self.assertEqual(cmd["command"], "python tests")
        self.assertEqual(cmd["status"], "verified")

    def test_planner_uses_allowed_command_not_unavailable_tool(self):
        name = "af42-plan"
        d = _make(name)
        snap = project_snapshot.build(name)
        plan = agent_planner.create(name, "fix and test the app", snapshot=snap)
        verify = next(s for s in plan["steps"] if s["id"] == "verify")
        self.assertTrue(verify["strategy"].startswith("command:"))
        self.assertIn(plan["verify_command"], ("python tests", "pytest -q"))
        self.assertIsInstance(plan["project_model"]["environment"], dict)
        # No server/network command enters an executable plan.
        all_strategy = " ".join(s["strategy"] for s in plan["steps"])
        self.assertNotIn("curl", all_strategy)
        self.assertNotIn("server.py", all_strategy)
        self.assertTrue(d.exists())

    def test_refresh_updates_environment_signature(self):
        name = "af42-refresh"
        _make(name)
        old = project_snapshot.environment(name, refresh=True)
        new = project_snapshot.environment(name, refresh=True)
        self.assertEqual(old["signature"], new["signature"])


if __name__ == "__main__":
    unittest.main()
