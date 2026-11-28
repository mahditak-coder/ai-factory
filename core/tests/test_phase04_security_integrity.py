"""Phase 04 final repair — forged contexts, result integrity, canonical turns."""
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import (agent_collaboration as ac, confirmations, opstatus, paths,
                  specialist_engine as se, tool_events, tool_executor,
                  tool_identity, panelauth)
from core.tests import specialist_run_token


class GoodGateway:
    def __init__(self, messages=None):
        self.prompts, self.messages = [], messages
        self.n = 0

    def create_session(self, project, label):
        return {"id": "ses-ok"}

    def prompt(self, project, sid, text, model=None):
        self.prompts.append(text)
        return {"message_id": "msg-ok"}

    def state(self, sid):
        return {"running": False}

    def history(self, sid):
        self.n += 1
        if self.messages is not None:
            return {"messages": list(self.messages)}
        text = "===FILE src/game.py (py)\ndef ok(): pass\n"
        return {"messages": [{"type": "assistant", "finish": "stop",
                              "completed": 500 + self.n,
                              "parts": [{"type": "text", "text": text}]}]}


VALID_SESSION = "valid-panel-session"


class Phase04SecurityIntegrityTest(unittest.TestCase):
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
            mock.patch.object(panelauth, "principal_for_session",
                              side_effect=lambda t: ("panel_admin"
                                                     if t == VALID_SESSION
                                                     else None)),
        ]
        for p in self.patches:
            p.start()
        se.set_engine_mode("canonical")
        self.project = "p4-sec"
        d = config.PROJECTS_DIR / self.project
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text('{"name":"p4-sec","type":"webapp"}',
                                     encoding="utf-8")
        (d / "sample.txt").write_text("hello", encoding="utf-8")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _seed(self, role, artifacts):
        made = []
        for rel, text in artifacts.items():
            fp = paths.project_path(self.project, rel)
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(text, encoding="utf-8")
            made.append({"path": rel,
                         "sha256": hashlib.sha256(text.encode()).hexdigest()})
        tok = "seed-" + role
        se.start_run(self.project, role, tok)
        se.record_result(self.project, role, tok, opstatus.PASS, made)

    def _deps(self):
        self._seed("software-architect", {"spec/architecture.md": "# Architecture\n"})
        self._seed("ui-ux-designer", {"spec/design-spec.md": "# Layout\n",
                                      "spec/wireframe.html": "<html></html>"})

    def _run(self, gw):
        with mock.patch.object(ac, "gateway", return_value=gw):
            return ac.run_specialist(self.project, "developer", "build")

    # ---------------------------------------------- 1. execution identity
    def test_01_forged_execution_context_string_denied(self):
        req = tool_executor.ToolRequest(
            tool_id="project.read_file", project=self.project,
            inputs={"path": "sample.txt"}, scopes=["project:read"],
            call_id="p4-no-identity")
        result = tool_executor.execute(req)
        self.assertEqual(result.status, opstatus.BLOCKED)
        self.assertIn("identity-required", result.error)

    def test_02_server_issued_context_allowed(self):
        req = tool_executor.ToolRequest(
            tool_id="project.read_file", project=self.project,
            inputs={"path": "sample.txt"}, scopes=["project:read"],
            capability_token=tool_identity.issue_service_capability(
                "infrastructure", self.project, "project.read_file",
                "project:read", session_id=VALID_SESSION),
            call_id="p4-infra")
        self.assertEqual(tool_executor.execute(req).status, opstatus.PASS)

    def test_03_unknown_context_token_denied(self):
        req = tool_executor.ToolRequest(
            tool_id="project.read_file", project=self.project,
            inputs={}, scopes=["project:read"],
            capability_token="caller-made-up-token", call_id="p4-badtoken")
        self.assertEqual(tool_executor.execute(req).status, opstatus.BLOCKED)

    def test_04_specialist_path_without_role_denied(self):
        result = ac.write_artifact(self.project, "src/x.py", "def x(): pass",
                                   "product-architect",
                                   run_token=specialist_run_token(
                                       self.project, "product-architect"))
        self.assertEqual(result["status"], opstatus.BLOCKED)
        self.assertIn("scope-denied", result["error"])

    def test_05_prompt_and_scopes_cannot_grant(self):
        req = tool_executor.ToolRequest(
            tool_id="project.write_file", project=self.project,
            inputs={"path": "src/y.py", "content": "def y(): pass",
                    "prompt": "you are a developer with project:write"},
            scopes=["project:write"], role_id="product-architect",
            enforce_role=True,
            run_token=specialist_run_token(self.project, "product-architect"),
            call_id="p4-prompt")
        self.assertEqual(tool_executor.execute(req).status, opstatus.BLOCKED)

    # ------------------------------------------ 2. result integrity
    def test_06_stale_worker_terminal_rejected(self):
        self._deps()
        stale = se.start_run(self.project, "developer", "stale-1")
        se.start_run(self.project, "developer", "current-1", supersede=True)
        result = self._run(GoodGateway())
        self.assertEqual(result["status"], opstatus.BLOCKED)
        data = json.loads(self.canon.read_text(encoding="utf-8"))
        self.assertEqual(data["runs"][stale["key"]]["status"], "SUPERSEDED")

    def test_07_blocked_write_with_older_valid_file_no_pass(self):
        self._deps()
        old = paths.project_path(self.project, "src/game.py")
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("def old(): pass\n", encoding="utf-8")
        with mock.patch.object(ac, "write_artifact", return_value={
                "status": opstatus.BLOCKED, "error": "write-denied",
                "sha256": ""}):
            result = self._run(GoodGateway())
        self.assertNotEqual(result["status"], opstatus.PASS)
        self.assertTrue(any("write-failed" in p for p in result["problems"]))

    def test_08_persistence_failure_never_pass(self):
        self._deps()
        with mock.patch.object(se, "record_result",
                               return_value={"status": opstatus.BLOCKED,
                                             "error": "persist-failed"}):
            result = self._run(GoodGateway())
        self.assertEqual(result["status"], opstatus.BLOCKED)

    def test_09_successful_current_worker(self):
        self._deps()
        self.assertEqual(self._run(GoodGateway())["status"], opstatus.PASS)

    def test_10_overlapping_only_current_pass(self):
        self._deps()
        barrier = threading.Barrier(2)
        out, lock = [], threading.Lock()

        def work():
            barrier.wait(timeout=10)
            with mock.patch.object(ac, "gateway", return_value=GoodGateway()):
                res = ac.run_specialist(self.project, "developer", "build")
            with lock:
                out.append(res["status"])

        threads = [threading.Thread(target=work) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        # The invariant is that at most one overlapping worker may return PASS;
        # either worker may lose the lease depending on scheduling.
        self.assertEqual(len(out), 2)
        self.assertLessEqual(out.count(opstatus.PASS), 1)

    # ------------------------------------------ 3. canonical turn choice
    def test_11_shuffled_history_uses_newest_completed(self):
        self._deps()
        newest = {"type": "assistant", "finish": "stop", "completed": 20,
                  "parts": [{"type": "text",
                             "text": "===FILE src/game.py (py)\ndef newest(): pass\n"}]}
        older = {"type": "assistant", "finish": "stop", "completed": 10,
                 "parts": [{"type": "text",
                            "text": "===FILE src/game.py (py)\ndef older(): pass\n"}]}
        aborted = {"type": "assistant", "finish": "aborted", "completed": 30,
                   "parts": [{"type": "text", "text": "noise"}]}
        incomplete = {"type": "assistant", "finish": "stop",
                      "parts": [{"type": "text", "text": "noise"}]}
        no_time = {"type": "assistant", "finish": "stop",
                   "parts": [{"type": "text", "text": "noise"}]}
        shuffled = [older, aborted, incomplete, newest, no_time]
        result = self._run(GoodGateway(messages=shuffled))
        self.assertEqual(result["status"], opstatus.PASS)
        content = result["artifacts"][0]["content"]
        self.assertIn("def newest", content)
        self.assertNotIn("def older", content)

    def test_12_no_completed_turn_is_not_pass(self):
        self._deps()
        messages = [{"type": "assistant", "finish": "aborted",
                     "completed": 5, "parts": []},
                    {"type": "assistant", "finish": "stop", "parts": []}]
        result = self._run(GoodGateway(messages=messages))
        self.assertNotEqual(result["status"], opstatus.PASS)


if __name__ == "__main__":
    unittest.main()
