"""Real ToolExecutor + lease_fence boundary tests (no mocked executor)."""
import hashlib
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import (agent_collaboration as ac, confirmations, opstatus, paths,
                  specialist_engine as se, tool_events, tool_executor,
                  tool_registry)


class RealBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.canon = self.base / "canon.json"
        self.legacy = self.base / "legacy.json"
        self.legacy.write_text("[]", encoding="utf-8")
        self.flags = self.base / "flags.json"
        self.conf = self.base / "confirmations.json"
        self.ev = self.base / "events.json"
        self.rc = self.base / "receipts.json"
        self.ev.write_text("[]", encoding="utf-8")
        self.rc.write_text("{}", encoding="utf-8")
        self.patches = [
            mock.patch.object(se, "_STORE", self.canon),
            mock.patch.object(se, "_LEGACY_STORE", self.legacy),
            mock.patch.object(se, "_FLAGS", self.flags),
            mock.patch.object(ac, "_STORE", self.legacy),
            mock.patch.object(confirmations, "_STORE", self.conf),
            mock.patch.object(tool_events, "_STORE", self.ev),
            mock.patch.object(tool_events, "_RECEIPT_STORE", self.rc),
        ]
        for p in self.patches:
            p.start()
        self._saved_specs = {tid: tool_registry.registry().get(tid)
                             for tid in ("p4.boundary", "project.write_file",
                                         "tests.run", "security.scan")}
        se.set_engine_mode("canonical")
        self.project = "p4-boundary"
        d = config.PROJECTS_DIR / self.project
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text('{"name":"p4-boundary","type":"webapp"}',
                                     encoding="utf-8")
        self.token = se.start_run(self.project, "developer", "boundary-1")["token"]

    def tearDown(self):
        for tid, spec in self._saved_specs.items():
            if spec is not None:
                tool_registry.registry().register(spec)
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _tool(self, tid="p4.boundary", scope="project:read", adapter=None):
        calls = []
        tool_registry.registry().register(tool_registry.ToolSpec(
            tid, "P4", "test", "test", "test", scope, {}, timeout_s=10,
            adapter=adapter or (lambda p, i, **k: calls.append(1) or {"ok": 1})))
        return calls

    def _req(self, tid="p4.boundary", token="boundary-1", project=None,
             call_id="boundary-call"):
        return tool_executor.ToolRequest(
            tool_id=tid, project=project or self.project, inputs={},
            scopes=["project:read"], role_id="developer", enforce_role=True,
            run_token=token, call_id=call_id)

    # ------------------------------------------------------ identity cases
    def test_01_missing_token_never_reaches_adapter(self):
        calls = self._tool()
        result = tool_executor.execute(self._req(token=""))
        self.assertEqual(result.status, opstatus.BLOCKED)
        self.assertIn("run-token-required", result.error)
        self.assertEqual(calls, [])

    def test_02_wrong_project_token_rejected(self):
        calls = self._tool()
        result = tool_executor.execute(self._req(project="p4-other"))
        self.assertEqual(result.status, opstatus.BLOCKED)
        self.assertEqual(calls, [])

    def test_03_superseded_token_rejected(self):
        calls = self._tool()
        se.start_run(self.project, "developer", "newer", supersede=True)
        result = tool_executor.execute(self._req())
        self.assertEqual(result.status, opstatus.BLOCKED)
        self.assertIn("stale", result.error)
        self.assertEqual(calls, [])

    def test_04_current_lease_executes(self):
        calls = self._tool()
        self.assertEqual(tool_executor.execute(
            self._req(call_id="boundary-ok")).status, opstatus.PASS)
        self.assertEqual(len(calls), 1)

    # ------------------------------------------- real fence concurrency
    def test_05_supersede_blocked_until_adapter_finishes(self):
        entered, release, attempted, superseded = (
            threading.Event(), threading.Event(), threading.Event(),
            threading.Event())
        calls = []

        def blocked_adapter(project, inputs, **kw):
            calls.append(1)
            entered.set()
            release.wait(timeout=15)
            return {"ok": 1}

        self._tool(adapter=blocked_adapter)
        outcome = {}

        def worker():
            outcome["result"] = tool_executor.execute(
                self._req(call_id="boundary-fence"))

        t = threading.Thread(target=worker)
        t.start()
        self.assertTrue(entered.wait(timeout=10))

        def superseder():
            attempted.set()
            se.start_run(self.project, "developer", "supersede-1",
                         supersede=True)
            superseded.set()

        s = threading.Thread(target=superseder)
        s.start()
        self.assertTrue(attempted.wait(timeout=10))
        self.assertFalse(superseded.is_set())  # fence still held
        release.set()
        t.join(timeout=15)
        s.join(timeout=15)
        self.assertEqual(outcome["result"].status, opstatus.PASS)
        self.assertTrue(superseded.is_set())

    def test_06_no_artifact_write_after_supersession(self):
        tid = "project.write_file"
        calls = []
        tool_registry.registry().register(tool_registry.ToolSpec(
            tid, "P4", "test", "test", "test", "project:write", {},
            timeout_s=10,
            adapter=lambda p, i, **k: calls.append(i.get("path")) or
            {"ok": 1, "sha256": "x", "size": 1}))
        se.start_run(self.project, "developer", "newer-2", supersede=True)
        result = ac.write_artifact(self.project, "src/blocked.py",
                                   "def blocked(): pass", "developer",
                                   run_token=self.token)
        self.assertEqual(result["status"], opstatus.BLOCKED)
        self.assertEqual(calls, [])

    def test_07_adapter_exception_not_pass(self):
        def boom(project, inputs, **kw):
            raise RuntimeError("adapter-failed")
        self._tool(adapter=boom)
        result = tool_executor.execute(self._req(call_id="boundary-exc"))
        self.assertNotEqual(result.status, opstatus.PASS)

    def test_08_receipt_failure_blocks_before_adapter(self):
        calls = self._tool()
        with mock.patch.object(tool_events, "claim",
                               side_effect=tool_events.ReceiptStoreError(
                                   "corrupt")):
            result = tool_executor.execute(self._req(call_id="boundary-rc"))
        self.assertEqual(result.status, opstatus.BLOCKED)
        self.assertEqual(calls, [])

    # ---------------------------------------------- concurrent QA real path
    def test_09_concurrent_qa_at_most_one_pass(self):
        for tid, scope in (("tests.run", "tests:run"),
                           ("security.scan", "security:read")):
            tool_registry.registry().register(tool_registry.ToolSpec(
                tid, "P4", "test", "test", "test", scope, {}, timeout_s=10,
                adapter=lambda p, i, **k: {"status": opstatus.PASS,
                                           "ok": True, "output": {},
                                           "error": "", "call_id": k.get(
                                               "call_id", "")}))
        text = "def main(): pass\n"
        fp = paths.project_path(self.project, "src/game.py")
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(text, encoding="utf-8")
        dep = "qa-dep"
        se.start_run(self.project, "developer", dep, supersede=True)
        se.record_result(self.project, "developer", dep, opstatus.PASS,
                         [{"path": "src/game.py",
                           "sha256": hashlib.sha256(text.encode()).hexdigest()}])
        barrier = threading.Barrier(2)
        out, lock = [], threading.Lock()

        def work():
            barrier.wait(timeout=10)
            result = ac.independent_qa(self.project)
            with lock:
                out.append(result["status"])

        threads = [threading.Thread(target=work) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(len(out), 2)
        self.assertLessEqual(out.count(opstatus.PASS), 1)


if __name__ == "__main__":
    unittest.main()
