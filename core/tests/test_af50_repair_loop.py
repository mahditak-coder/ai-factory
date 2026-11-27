"""AF-50 — bounded repair loop behaviour (UNIT with mocks, not E2E proof)."""
import unittest
from unittest import mock

from core import factory
from core.engines.registry import EngineError

FINDING = [{"file": "a.java", "line": 9, "kind": "error",
            "message": "no suitable method found"}]


class RepairLoopTest(unittest.TestCase):
    def test_repeated_identical_error_stops_without_new_turn(self):
        def always_fail():
            raise EngineError(500, "build failed", findings=FINDING)
        with mock.patch.object(factory, "generation_session", return_value="ses_x"), \
             mock.patch.object(factory, "state", return_value={"running": False}), \
             mock.patch.object(factory, "fix_request",
                               return_value={"session_id": "ses_x",
                                             "message_id": "m1"}) as fix, \
             mock.patch.object(factory, "wait_done", return_value=True):
            with self.assertRaises(EngineError) as ctx:
                factory.android_build_repair("p", always_fail)
        self.assertEqual(ctx.exception.status, 422)
        self.assertIn("تکراری", ctx.exception.message)
        self.assertEqual(fix.call_count, 1)

    def test_busy_session_is_interrupted_not_prompted(self):
        states = [{"running": True}, {"running": False}]
        def once_then_ok():
            if not once_then_ok.called:
                once_then_ok.called = True
                raise EngineError(500, "build failed", findings=FINDING)
            return {"ok": True, "output_dir": "/tmp/x"}
        once_then_ok.called = False
        with mock.patch.object(factory, "generation_session", return_value="ses_x"), \
             mock.patch.object(factory, "state", side_effect=states), \
             mock.patch.object(factory, "interrupt") as intr, \
             mock.patch.object(factory.time, "sleep"), \
             mock.patch.object(factory, "fix_request",
                               return_value={"session_id": "ses_x",
                                             "message_id": "m1"}) as fix, \
             mock.patch.object(factory, "wait_done", return_value=True):
            rep = factory.android_build_repair("p", once_then_ok)
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["repair_cycles"], 1)
        intr.assert_called_once_with("ses_x")
        fix.assert_called_once()


if __name__ == "__main__":
    unittest.main()
