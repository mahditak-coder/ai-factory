"""AF-54 — non-destructive batch archive of stale ASKs (UNIT)."""
import tempfile, unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import tasks


class ArchiveTest(unittest.TestCase):
    def test_archive_keeps_all_ask_data(self):
        name = "af54-archive"
        d = config.PROJECTS_DIR / name
        d.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".json") as tf:
            store = Path(tf.name)
            store.write_text("[]", encoding="utf-8")
            with mock.patch.object(tasks, "_STORE", store):
                t = tasks.add_task(name, "push to github")
                t = tasks.ask_task(t["id"], "سؤال", ["بله"], "tok123", "CONFIRM")
                before = dict(t["ask"])
                r = tasks.archive_asks([t["id"]], note="old")
                after = tasks.get_task(t["id"])
            self.assertEqual(r["count"], 1)
            self.assertTrue(after["ask"]["archived"])
            for k in ("question", "options", "token", "context", "decision"):
                self.assertEqual(after["ask"].get(k), before.get(k))
            self.assertEqual(after["state"], "awaiting_user")  # never auto-resumed


if __name__ == "__main__":
    unittest.main()
