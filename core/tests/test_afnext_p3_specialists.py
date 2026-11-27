"""AF NEXT P3 — specialist contracts, dependencies and handoff (UNIT)."""
import tempfile, unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import (action_gate, agent_collaboration as collab, opstatus,
                  orchestrator, specialists, tool_events)


def fixture(name="afp3-unit"):
    d = config.PROJECTS_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text('{"name":"%s","type":"webapp"}' % name,
                                 encoding="utf-8")
    return d


class ContractTest(unittest.TestCase):
    def test_nine_roles_have_complete_contracts(self):
        self.assertEqual(len(specialists.ROLES), 9)
        ids = {r.id for r in specialists.ROLES}
        self.assertEqual(ids, {"product-architect", "software-architect",
            "ui-ux-designer", "game-designer", "visual-artist",
            "audio-designer", "developer", "qa-engineer", "security-reviewer"})
        for r in specialists.ROLES:
            self.assertTrue(r.inputs and r.responsibilities and r.allowed_scopes)
            self.assertTrue(r.artifacts and r.acceptance, r.id)
            for a in r.artifacts:
                self.assertTrue(a.path and a.format, r.id)

    def test_dependencies_and_parallel_groups(self):
        self.assertFalse(collab.can_start("none", "developer")[0])
        groups = specialists.parallel_groups("webapp")
        self.assertEqual(groups[0], ["product-architect"])
        flat = [x for g in groups for x in g]
        self.assertLess(flat.index("software-architect"), flat.index("developer"))
        self.assertLess(flat.index("ui-ux-designer"), flat.index("developer"))
        self.assertLess(flat.index("developer"), flat.index("qa-engineer"))
        self.assertLess(flat.index("developer"), flat.index("security-reviewer"))
        # independent roles may share a group
        self.assertTrue(any("game-designer" in g and "visual-artist" in g
                            for g in groups))

    def test_orchestrator_plan_is_dag_aware(self):
        p = orchestrator.specialist_plan("none", "build a game", "webapp")
        self.assertTrue(any(x["role"] == "developer" and not x["can_start"]
                            for x in p))
        self.assertTrue(any(x["role"] == "product-architect" and x["can_start"]
                            for x in p))


class ArtifactTest(unittest.TestCase):
    def setUp(self):
        fixture()
        self.tf = tempfile.NamedTemporaryFile(suffix=".json")
        Path(self.tf.name).write_text("[]", encoding="utf-8")
        self.p1 = mock.patch.object(collab, "_STORE", Path(self.tf.name))
        self.tf2 = tempfile.NamedTemporaryFile(suffix=".json")
        Path(self.tf2.name).write_text("[]", encoding="utf-8")
        self.p2 = mock.patch.object(tool_events, "_STORE", Path(self.tf2.name))
        self.p1.start(); self.p2.start()

    def tearDown(self):
        self.p1.stop(); self.p2.stop(); self.tf.close(); self.tf2.close()

    def test_artifact_written_through_registry_and_versioned(self):
        r = collab.write_artifact("afp3-unit", "spec/product-brief.md",
                                 "## Target User\nx\n## Scope\ny\n## Success Criteria\nz\n",
                                 "product-architect")
        self.assertEqual(r["status"], opstatus.PASS)
        first = collab.record("afp3-unit", "product-architect", opstatus.PASS,
                              artifacts=[r])
        second = collab.record("afp3-unit", "product-architect", opstatus.PASS,
                               artifacts=[r])
        self.assertEqual(second["version"], first["version"] + 1)
        ok, problems = collab.acceptance("afp3-unit", "product-architect")
        self.assertTrue(ok, problems)

    def test_acceptance_rejects_missing_sections(self):
        # keep this test isolated from the artifact written by the previous test
        import shutil
        shutil.rmtree(config.PROJECTS_DIR / "afp3-unit", ignore_errors=True)
        fixture()
        ok, problems = collab.acceptance("afp3-unit", "product-architect")
        self.assertFalse(ok)
        self.assertTrue(any("missing" in p for p in problems))

    def test_dependency_unlocks_after_acceptance(self):
        self.assertFalse(collab.can_start("afp3-unit", "software-architect")[0])
        collab.record("afp3-unit", "product-architect", opstatus.PASS)
        self.assertTrue(collab.can_start("afp3-unit", "software-architect")[0])

    def test_conflict_and_decision_are_recorded(self):
        collab.record("afp3-unit", "product-architect", opstatus.PASS)
        collab.record("afp3-unit", "product-architect", opstatus.FAIL)
        c = collab.conflicts("afp3-unit")
        self.assertTrue(any(x["role"] == "product-architect" for x in c))
        d = collab.decide("afp3-unit", "product-architect", "keep-v1", "conflict")
        self.assertEqual(d["evidence"]["decision"], "keep-v1")
        self.assertNotEqual(d["status"], opstatus.PASS)


if __name__ == "__main__":
    unittest.main()
