"""AF NEXT P1 — ModelGateway / EngineAdapter contracts (UNIT)."""
import unittest

from core import engine_adapter, factory, model_gateway, opstatus, orchestrator
from core.engine_adapter import EngineAdapter, EngineResult
from core.model_gateway import ModelGateway, SessionStateView, gateway, set_gateway


class GatewayTest(unittest.TestCase):
    def test_default_gateway_is_real_opencode_adapter(self):
        set_gateway(None)
        g = gateway()
        self.assertIsInstance(g, ModelGateway)
        self.assertEqual(g.name, "opencode")
        self.assertIs(g, gateway())          # singleton, no scattered instances

    def test_module_seams_delegate_to_gateway(self):
        set_gateway(None)
        self.assertTrue(callable(factory.prompt))
        self.assertTrue(callable(factory.create_session))
        self.assertTrue(callable(orchestrator.oc_state))

    def test_session_state_view_maps_real_fields(self):
        v = SessionStateView.from_dict({
            "session": "ses_x", "outcome": "interrupted", "running": False,
            "busy": 5, "idle": 9, "pending_permissions": ["per_1"],
            "awaiting_permission": True, "last_error": {"type": "aborted"}})
        self.assertEqual(v.session, "ses_x")
        self.assertFalse(v.running)
        self.assertEqual(v.pending_permissions, ["per_1"])
        self.assertTrue(v.awaiting_permission)
        self.assertEqual(v.last_error["type"], "aborted")

    def test_set_gateway_restores_default(self):
        class Fake:
            name = "fake"
        set_gateway(Fake())
        self.assertEqual(gateway().name, "fake")
        set_gateway(None)
        self.assertEqual(gateway().name, "opencode")


class EngineAdapterTest(unittest.TestCase):
    def test_android_adapter_is_real_and_scoped(self):
        a = engine_adapter.adapter_for("android")
        self.assertIsInstance(a, EngineAdapter)
        self.assertEqual(a.type_id, "android")
        self.assertIsNone(engine_adapter.adapter_for("godot-not-installed"))

    def test_engine_result_is_honest(self):
        r = EngineResult.fail("real failure")
        self.assertFalse(r.ok)
        self.assertEqual(r.status, opstatus.FAIL)
        self.assertEqual(r.error, "real failure")


if __name__ == "__main__":
    unittest.main()
