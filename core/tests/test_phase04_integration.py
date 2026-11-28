"""Phase 04 final integration — actual run_specialist, write_artifact and leases."""
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
                  tool_identity)
from core.tests import specialist_run_token

VALID_SESSION = "panel-session"


class FakeGateway:
    def __init__(self, artifact_text="def main():\n    return True\n",
                 long_producer=None):
        self.prompts = []
        self.artifact_text = artifact_text
        self.long_producer = long_producer or ""
        self.counter = 0

    def create_session(self, project, label):
        return {"id": "ses-test-" + label}

    def prompt(self, project, sid, text, model=None):
        self.prompts.append(text)
        return {"message_id": "msg-test"}

    def state(self, sid):
        return {"running": False}

    def history(self, sid):
        self.counter += 1
        text = "===FILE src/game.py (py)\n" + self.artifact_text
        return {"messages": [{"type": "assistant", "finish": "stop",
                              "completed": self.base_time + self.counter,
                              "parts": [{"type": "text", "text": text}]}]}

    base_time = 1000


class Phase04IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.canon = self.base / "canonical.json"
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
            mock.patch("core.panelauth.principal_for_session",
                       side_effect=lambda t: ("panel_admin"
                                              if t == VALID_SESSION else None)),
        ]
        for p in self.patches:
            p.start()
        se.set_engine_mode("canonical")
        self.project = "p4-int"
        d = config.PROJECTS_DIR / self.project
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text('{"name":"p4-int","type":"webapp"}',
                                     encoding="utf-8")
        (d / "sample.txt").write_text("hello", encoding="utf-8")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _write(self, rel, text):
        fp = paths.project_path(self.project, rel)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(text, encoding="utf-8")
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _seed_producer(self, role, artifacts):
        made = []
        for rel, text in artifacts.items():
            made.append({"path": rel, "sha256": self._write(rel, text)})
        token = "seed-" + role
        se.start_run(self.project, role, token)
        se.record_result(self.project, role, token, opstatus.PASS, made)
        return made

    def _seed_developer_deps(self, arch_text="# Architecture\n"):
        self._seed_producer("software-architect",
                            {"spec/architecture.md": arch_text})
        self._seed_producer("ui-ux-designer",
                            {"spec/design-spec.md": "# Layout\n",
                             "spec/wireframe.html": "<html></html>"})

    def _gateway(self, gw):
        return mock.patch.object(ac, "gateway", return_value=gw)

    # ------------------------------------------ 1. mandatory role auth
    def test_01_write_artifact_denies_unauthorized_role(self):
        result = ac.write_artifact(self.project, "src/evil.py",
                                   "def evil(): pass", "product-architect",
                                   session_id=VALID_SESSION,
                                   run_token=specialist_run_token(
                                       self.project, "product-architect"))
        self.assertEqual(result["status"], opstatus.BLOCKED)
        self.assertIn("scope-denied", result["error"])

    def test_02_write_artifact_with_valid_role_and_session(self):
        result = ac.write_artifact(self.project, "src/ok.py", "def ok(): pass",
                                   "developer", session_id=VALID_SESSION,
                                   run_token=specialist_run_token(self.project,
                                                                  "developer"))
        self.assertEqual(result["status"], opstatus.PASS)

    def test_03_non_specialist_context_still_works(self):
        req = tool_executor.ToolRequest(
            tool_id="project.read_file", project=self.project,
            inputs={"path": "sample.txt"}, scopes=["project:read"],
            capability_token=tool_identity.issue_service_capability(
                "infrastructure", self.project, "project.read_file",
                "project:read", session_id=VALID_SESSION),
            call_id="p4-nonspec")
        self.assertEqual(tool_executor.execute(req).status, opstatus.PASS)

    def test_04_ambiguous_context_denied(self):
        req = tool_executor.ToolRequest(
            tool_id="project.read_file", project=self.project,
            inputs={"path": "sample.txt"}, scopes=["project:read"],
            role_id="developer", enforce_role=True,
            run_token=specialist_run_token(self.project, "developer"),
            capability_token=tool_identity.issue_service_capability(
                "infrastructure", self.project, "project.read_file",
                "project:read", session_id=VALID_SESSION),
            call_id="p4-ambiguous")
        self.assertEqual(tool_executor.execute(req).status, opstatus.BLOCKED)

    # ----------------------------------------- 2. verified handoff integration
    def test_05_run_specialist_uses_verified_handoff(self):
        self._seed_developer_deps()
        gw = FakeGateway()
        with self._gateway(gw):
            result = ac.run_specialist(self.project, "developer", "build")
        self.assertEqual(result["status"], opstatus.PASS)
        self.assertTrue(gw.prompts)
        self.assertIn("Architecture", gw.prompts[0])

    def test_06_run_specialist_rejects_tampered_producer(self):
        self._seed_developer_deps()
        self._write("spec/architecture.md", "tampered content")
        gw = FakeGateway()
        with self._gateway(gw):
            result = ac.run_specialist(self.project, "developer", "build")
        self.assertEqual(result["status"], opstatus.BLOCKED)
        self.assertEqual(result["error"], "verified-handoff-failed")
        self.assertFalse(gw.prompts)

    def test_07_long_producer_text_not_truncated(self):
        long_text = "# Architecture\n" + ("طولانی" * 3000)
        self._seed_developer_deps(long_text)
        gw = FakeGateway(long_producer=long_text)
        with self._gateway(gw):
            result = ac.run_specialist(self.project, "developer", "build")
        self.assertEqual(result["status"], opstatus.PASS)
        self.assertIn("طولانی" * 50, gw.prompts[0])

    def test_08_missing_producer_record_blocks(self):
        gw = FakeGateway()
        with self._gateway(gw):
            result = ac.run_specialist(self.project, "developer", "build")
        self.assertEqual(result["status"], opstatus.BLOCKED)
        self.assertIn("dependency-not-accepted", result["error"])

    # ------------------------------------------- 3. real run leases
    def test_09_overlapping_specialists_only_one_current(self):
        self._seed_developer_deps()
        barrier = threading.Barrier(2)
        results = []
        lock = threading.Lock()

        def work():
            barrier.wait(timeout=10)
            res = ac.run_specialist(self.project, "developer", "build")
            with lock:
                results.append(res["status"])

        threads = [threading.Thread(target=work) for _ in range(2)]
        gw = FakeGateway()
        with self._gateway(gw):
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
        self.assertEqual(results.count(opstatus.PASS), 1)
        data = json.loads(self.canon.read_text(encoding="utf-8"))
        self.assertEqual([r["status"] for r in data["runs"].values()
                          if r.get("role_id") == "developer" and
                          r["status"] == opstatus.PASS].count(opstatus.PASS), 1)

    def test_10_stale_lease_cannot_produce_pass(self):
        self._seed_developer_deps()
        stale = se.start_run(self.project, "developer", "stale-token")
        se.start_run(self.project, "developer", "current-token", supersede=True)
        blocked = se.record_result(self.project, "developer", "stale-token",
                                   opstatus.PASS)
        self.assertEqual(blocked["status"], opstatus.BLOCKED)
        gw = FakeGateway()
        with self._gateway(gw):
            res = ac.run_specialist(self.project, "developer", "build")
        self.assertEqual(res["status"], opstatus.BLOCKED)
        data = json.loads(self.canon.read_text(encoding="utf-8"))
        self.assertEqual(data["runs"][stale["key"]]["status"], "SUPERSEDED")

    def test_11_legacy_mode_keeps_legacy_handoff(self):
        self._seed_developer_deps()
        se.set_engine_mode("legacy")
        try:
            gw = FakeGateway()
            with self._gateway(gw):
                res = ac.run_specialist(self.project, "developer", "build")
            self.assertEqual(res["status"], opstatus.PASS)
        finally:
            se.set_engine_mode("canonical")


if __name__ == "__main__":
    unittest.main()
