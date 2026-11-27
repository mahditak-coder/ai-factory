"""AF-43 — verified path grounding and project-scoped execution (UNIT)."""
import json
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import boundary, factory, opstatus, project_snapshot


def _make(name: str, node: bool = False) -> Path:
    d = config.PROJECTS_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps({"name": name, "type": "webpage"}),
                                 encoding="utf-8")
    if node:
        (d / "package.json").write_text('{"scripts":{"test":"node --test"}}',
                                        encoding="utf-8")
        (d / "server.js").write_text('function greet(){return "old"}\n',
                                     encoding="utf-8")
        (d / "tests").mkdir(exist_ok=True)
        (d / "tests/a.test.js").write_text('const test=require("node:test");test("a",()=>{});',
                                           encoding="utf-8")
    else:
        (d / "server.py").write_text("def main(): return 1\n", encoding="utf-8")
        (d / "app.py").write_text("def compute(x): return x\n", encoding="utf-8")
        (d / "tests").mkdir(exist_ok=True)
        (d / "tests/test_a.py").write_text(
            "from app import compute\n\ndef test_a(): assert compute(1)==1\n\ntest_a()\n",
            encoding="utf-8")
    return d


class PathGroundingTest(unittest.TestCase):
    def test_verified_paths_only_and_scoped_discovery(self):
        name = "af43-paths"
        d = _make(name)
        pm = project_snapshot.path_map(name)
        self.assertEqual(pm["status"], "verified")
        root = (pm["root"] or {}).get("value")
        self.assertEqual(root, str(d.resolve()))
        rels = [f["rel"] for f in (pm["files"] or {}).get("value") or []]
        self.assertIn("app.py", rels)
        self.assertIn("tests/test_a.py", rels)
        self.assertNotIn("core/factory.py", rels)
        disc = (pm["discovery"] or {}).get("value") or []
        self.assertTrue(any(x.get("command") == "pwd" and
                            x.get("decision") == "once" for x in disc))
        self.assertTrue(any(x.get("command") == "ls -la" and
                            x.get("decision") == "once" for x in disc))

    def test_execution_boundary_rejects_outside_and_traversal(self):
        name = "af43-boundary"
        d = _make(name)
        outside = str(Path(config.PROJECT_ROOT) / "core" / "factory.py")
        self.assertEqual(boundary.headless_permission(
            "shell", "grep -rn title %s" % outside, name), "reject")
        self.assertEqual(boundary.headless_permission(
            "shell", "cat ../../etc/passwd", name), "reject")
        self.assertEqual(boundary.headless_permission(
            "shell", "python tests/test_a.py", name), "once")
        self.assertTrue(d.is_dir())

    def test_node_path_grounding(self):
        name = "af43-node"
        _make(name, node=True)
        pm = project_snapshot.path_map(name)
        rels = [f["rel"] for f in (pm["files"] or {}).get("value") or []]
        self.assertIn("server.js", rels)
        self.assertIn("tests/a.test.js", rels)

    def test_path_recovery_hint_uses_verified_files(self):
        name = "af43-recover"
        _make(name)
        errors = [{"file": "wrong.py", "kind": "missing",
                   "message": "No such file or directory: wrong.py"}]
        with mock.patch.object(factory, "perception_block", return_value="") as pb, \
             mock.patch.object(factory, "prompt", return_value={
                 "message_id": "m", "session_id": "s"}), \
             mock.patch.object(factory, "generation_session", return_value="s"):
            factory.fix_request(name, errors, cycle_no=2)
        instruction = pb.call_args.kwargs.get("task_text", "")
        self.assertIn("app.py", instruction)
        self.assertIn("مسیرهای تأییدشده", instruction)


if __name__ == "__main__":
    unittest.main()
