"""AF-56 — one-shot permission reply, expiry and honest session state (UNIT)."""
import unittest
from unittest import mock

from core import opencode_bridge as ob


class PermissionReplyTest(unittest.TestCase):
    def test_reply_once_skips_missing_and_repeats(self):
        sid = "ses_unit_missing"
        with mock.patch.object(ob, "permissions", return_value=[]), \
             mock.patch.object(ob, "permission_reply") as reply:
            self.assertFalse(ob.reply_once(sid, "per_x", "once"))
            reply.assert_not_called()
        req = [{"id": "per_y", "action": "shell"}]
        with mock.patch.object(ob, "permissions", return_value=req), \
             mock.patch.object(ob, "permission_reply") as reply:
            self.assertTrue(ob.reply_once(sid, "per_y", "once"))
            self.assertFalse(ob.reply_once(sid, "per_y", "once"))
            self.assertEqual(reply.call_count, 1)

    def test_expiry_uses_reject_with_explicit_message(self):
        sid = "ses_unit_expiry"
        with mock.patch.object(ob, "permissions", return_value=[{"id": "per_z"}]), \
             mock.patch.object(ob, "permission_reply") as reply:
            self.assertTrue(ob.expire_permission(sid, "per_z"))
            args = reply.call_args.args
            self.assertEqual(args[2], "reject")
            self.assertIn("منقضی", reply.call_args.kwargs["message"])


class StateTest(unittest.TestCase):
    def test_terminal_assistant_clears_stale_running_and_shows_pending(self):
        sid = "ses_unit_state"
        session = {"data": {"time": {"busy": 2000, "idle": 1000}}}
        message = {"data": [{"type": "assistant", "finish": "error",
                             "time": {"completed": 2100}}]}
        with mock.patch.object(ob, "ensure_running"), \
             mock.patch.object(ob, "_session_location", return_value="/p/projects/x"), \
             mock.patch.object(ob, "_request", side_effect=[session, message]), \
             mock.patch.object(ob, "permissions", return_value=[{"id": "per_q"}]):
            st = ob.state(sid)
        self.assertFalse(st["running"])
        self.assertTrue(st["awaiting_permission"])
        self.assertEqual(st["pending_permissions"], ["per_q"])


if __name__ == "__main__":
    unittest.main()
