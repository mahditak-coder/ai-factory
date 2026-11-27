"""AF-45 — multi-resource shell policy needed for real product builds (UNIT)."""
import json
import unittest

import core.config as config
from core import boundary


def _fixture(name: str):
    d = config.PROJECTS_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps({"name": name, "type": "webapp"}),
                                 encoding="utf-8")
    (d / "index.html").write_text("<html></html>", encoding="utf-8")
    return d


class ChainedShellPolicyTest(unittest.TestCase):
    def test_all_safe_resources_are_allowed(self):
        p = "af45-policy-ok"
        _fixture(p)
        # OpenCode splits a chained diagnostic into several resources.
        self.assertEqual(boundary.headless_permission(
            "shell", ["pwd", "ls -la", "cat meta.json", "cat README.md"], p),
            "once")

    def test_one_unsafe_resource_rejects_the_whole_request(self):
        p = "af45-policy-bad"
        _fixture(p)
        self.assertEqual(boundary.headless_permission(
            "shell", ["pwd", "ls -la", "curl http://evil.invalid"], p), "reject")
        self.assertEqual(boundary.headless_permission(
            "shell", ["pwd", "rm -rf /sdcard"], p), "reject")
        outside = str(config.PROJECT_ROOT / "core" / "factory.py")
        self.assertEqual(boundary.headless_permission(
            "shell", ["pwd", "cat " + outside], p), "reject")

    def test_readonly_discovery_commands_allowed(self):
        p = "af45-policy-discovery"
        _fixture(p)
        self.assertEqual(boundary.headless_permission(
            "shell", ["python3 --version", "command -v python3"], p), "once")
        self.assertEqual(boundary.headless_permission(
            "shell", "which python3", p), "once")
        self.assertEqual(boundary.headless_permission(
            "shell", "curl http://evil.invalid", p), "reject")

    def test_single_command_behaviour_unchanged(self):
        p = "af45-policy-single"
        _fixture(p)
        self.assertEqual(boundary.headless_permission(
            "shell", ["python tests"], p), "once")
        self.assertEqual(boundary.headless_permission(
            "shell", ["curl http://evil.invalid"], p), "reject")


if __name__ == "__main__":
    unittest.main()
