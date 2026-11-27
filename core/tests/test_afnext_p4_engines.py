"""AF NEXT P4 — six engine contracts, discovery and selection (UNIT)."""
import unittest

import core.config as config
from core import (engine_adapter, engine_catalog, engine_discovery,
                  engine_selection, opstatus, opstatus as st)


def fixture(name="afp4-unit", ptype="webapp"):
    d = config.PROJECTS_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text('{"name":"%s","type":"%s"}' % (name, ptype),
                                 encoding="utf-8")
    return d


class DiscoveryTest(unittest.TestCase):
    def test_status_vocabulary_and_real_tools(self):
        d = engine_discovery.report()
        self.assertIn(engine_discovery.AVAILABLE, d["status_model"])
        self.assertIn(engine_discovery.BLOCKED, d["status_model"])
        self.assertEqual(d["arch"], "aarch64")
        self.assertTrue(d["tools"]["python3"]["version"])
        self.assertEqual(d["tools"]["godot"]["status"], engine_discovery.BLOCKED)
        self.assertEqual(d["tools"]["xcodebuild"]["status"], engine_discovery.BLOCKED)


class ContractTest(unittest.TestCase):
    def test_six_profiles_have_full_contract(self):
        self.assertEqual({p.id for p in engine_catalog.PROFILES},
            {"android", "web_saas", "backend_api", "ai_agent", "game", "desktop"})
        for p in engine_catalog.PROFILES:
            for field in ("product_types", "capabilities", "limits", "tools",
                          "structure", "build", "preview", "test", "output",
                          "success"):
                self.assertTrue(getattr(p, field), "%s:%s" % (p.id, field))
                if field in ("product_types", "capabilities", "limits", "tools"):
                    self.assertIsInstance(getattr(p, field), tuple,
                                          "%s:%s must be a tuple" % (p.id, field))

    def test_adapter_for_every_engine_and_desktop_blocked(self):
        for eid in ("android", "web_saas", "backend_api", "ai_agent", "game",
                    "desktop"):
            a = engine_adapter.adapter_for(eid)
            self.assertIsInstance(a, engine_adapter.EngineAdapter, eid)
            self.assertTrue(hasattr(a, "profile"), eid)
        a = engine_adapter.adapter_for("desktop")
        d = a.build("x")
        self.assertEqual(d.status, opstatus.BLOCKED)
        self.assertFalse(d.ok)
        pkg = a.package("x", config.PROJECTS_DIR)
        self.assertEqual(pkg.status, opstatus.BLOCKED)
        self.assertFalse(pkg.ok)
        self.assertFalse(pkg.artifacts)


class SelectionTest(unittest.TestCase):
    def test_selection_uses_capability_not_only_installed(self):
        r = engine_selection.best("webapp", "build a landing page")
        self.assertEqual(r["engine"], "web_saas")
        self.assertEqual(r["status"], engine_discovery.AVAILABLE)
        a = engine_selection.best("android", "build an APK")
        self.assertEqual(a["engine"], "android")

    def test_three_d_requirement_is_not_pretended_as_2d(self):
        r = engine_selection.best("webapp", "build a 3D game with Godot")
        self.assertEqual(r["status"], engine_discovery.BLOCKED)
        self.assertTrue(any("3D" in n for n in r["notes"]))

    def test_desktop_blocked_with_reason(self):
        r = engine_selection.best("desktop", "build an exe")
        self.assertEqual(r["status"], engine_discovery.BLOCKED)
        self.assertTrue(r["blockers"])

    def test_architects_receive_capability_advice(self):
        from core import agent_collaboration
        fixture("afp4-advice", "webapp")
        advice = agent_collaboration.engine_advice(
            "afp4-advice", "build a 3D game with Godot")
        self.assertIn("game", advice)
        self.assertIn("BLOCKED", advice)
        self.assertIn("3D", advice)


if __name__ == "__main__":
    unittest.main()
