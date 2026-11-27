"""AF-39 — structured Project Model on real isolated projects (INTEGRATION).

Real files, real git where available, real stores. No model/tool mocks here.
"""
import json
import unittest
from pathlib import Path

import core.config as config
from core import agent_planner, project_snapshot


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _make(name: str, files: dict) -> Path:
    d = config.PROJECTS_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    _write(d, "meta.json", json.dumps({"name": name, "type": "webpage",
                                      "version": "1.0.0"}))
    for rel, text in files.items():
        _write(d, rel, text)
    return d


class ProjectModelTest(unittest.TestCase):
    def test_python_project_facts_are_source_tagged(self):
        name = "af39-python"
        d = _make(name, {
            "server.py": "import json\nfrom app import helper\nprint(json.dumps(helper()))\n",
            "app.py": "def helper():\n    return {'ok': True}\n",
            "requirements.txt": "requests==2.31.0\n",
            "README.md": "# Demo\n",
        })
        snap = project_snapshot.build(name)
        sec = snap["sections"]
        self.assertEqual(snap["status"], "ok")
        self.assertEqual(sec["entry"]["path"], "server.py")
        self.assertEqual(sec["stack"]["language"], "python")
        self.assertIn("requests", sec["dependencies"]["names"])
        self.assertEqual(sec["stack"]["status"], "ok")
        arch = sec["architecture"]
        edges = arch["edges"]["value"]
        self.assertTrue(any(e["to"].endswith("app.py") for e in edges), edges)
        self.assertEqual(arch["edges"]["status"], "verified")
        self.assertIn("server.py", arch["edges"]["source"])
        self.assertIn("README.md", sec["docs"]["docs"]["value"])
        self.assertEqual(sec["docs"]["docs"]["status"], "verified")
        plan = agent_planner.create(name, "improve the app", snapshot=snap)
        self.assertEqual(plan["type"], "webpage")
        self.assertTrue(plan["project_model"]["sections"])
        verify = next(s for s in plan["steps"] if s["id"] == "verify")
        # AF-42 — planner now selects a REAL executable command from the
        # environment model, not a structural Project Model key.
        self.assertEqual(verify["strategy"], "command:python tests")
        self.assertTrue((d / "meta.json").is_file())

    def test_node_and_monorepo_detection(self):
        name = "af39-node"
        d = _make(name, {
            "package.json": json.dumps({"scripts": {"test": "jest", "start": "node server.js"},
                                        "dependencies": {"express": "4.18.0"},
                                        "engines": {"node": ">=18"}}),
            "server.js": "const express = require('express');\n",
            "packages/a/package.json": json.dumps({"name": "a"}),
        })
        snap = project_snapshot.build(name)
        sec = snap["sections"]
        self.assertIn("npm:test", sec["commands"]["items"])
        self.assertEqual(sec["commands"]["items"]["npm:test"]["status"], "verified")
        self.assertTrue(sec["monorepo"]["monorepo"])
        self.assertEqual(sec["docs"]["limits"]["node"]["value"], ">=18")
        self.assertEqual(sec["docs"]["limits"]["node"]["status"], "verified")
        self.assertIn("express", sec["dependencies"]["names"])
        self.assertTrue((d / "packages/a/package.json").is_file())

    def test_unknown_and_incomplete_projects_are_honest(self):
        name = "af39-unknown"
        _make(name, {"notes.txt": "nothing runnable"})
        snap = project_snapshot.build(name)
        sec = snap["sections"]
        self.assertEqual(sec["stack"]["status"], "unavailable")
        self.assertEqual(sec["entry"]["status"], "unavailable")
        self.assertIn("unknown", str(sec["commands"]["items"]))

    def test_targeted_refresh_invalidates_changed_source(self):
        name = "af39-refresh"
        d = _make(name, {"app.py": "import os\n"})
        first = project_snapshot.build(name)
        self.assertTrue(first["sections"]["architecture"]["edges"]["value"] == [] or
                        isinstance(first["sections"]["architecture"]["edges"]["value"], list))
        # Change a source file that is an architecture source.
        _write(d, "app.py", "import json\nimport sys\n")
        project_snapshot.build(name)   # store fresh, source-tagged model first
        result = project_snapshot.invalidate(name, ["app.py"])
        self.assertGreaterEqual(result["invalidated"], 1)
        second = project_snapshot.build(name)
        self.assertEqual(second["status"], "ok")
        stored = project_snapshot.stored(name)
        self.assertIn("app.py", stored["signatures"])
        self.assertIsInstance(stored["model"], dict)

    def test_project_model_store_is_separate_from_general_memory(self):
        name = "af39-separated"
        _make(name, {"main.py": "print('hi')\n"})
        project_snapshot.build(name)
        self.assertTrue(config.PROJECT_ROOT.joinpath("data", "project_models.json").is_file())
        mem = config.PROJECT_ROOT.joinpath("data", "memory.json")
        if mem.is_file():
            self.assertNotIn("af39-separated", mem.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
