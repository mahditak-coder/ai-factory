"""AF-29 P0 — perception, tool observation, action gating, boundary policy.

Real production functions are called with real fixtures where possible.  Where
an EXTERNAL OpenCode/Git event must be substituted, the test is explicitly a
UNIT test with a representative event dict and is never reported as E2E proof.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import (action_gate, agent_events, boundary, factory, opencode_bridge,
                  opstatus, project_git, project_snapshot, smart_release as sr)


def _fixture(name: str, with_git: bool = False) -> Path:
    d = config.PROJECTS_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(
        json.dumps({"name": name, "type": "webpage", "version": "1.0.0"}),
        encoding="utf-8")
    (d / "index.html").write_text("<html><title>x</title><h1>x</h1></html>",
                                  encoding="utf-8")
    (d / "app.js").write_text("document.addEventListener('click',()=>{})",
                              encoding="utf-8")
    (d / "requirements.txt").write_text("json\n", encoding="utf-8")
    (d / ".env").write_text("ANTHROPIC_AUTH_TOKEN=LEAKME-1234567890",
                            encoding="utf-8")
    (d / "product.key").write_text("PRIVATE-KEY-LEAK", encoding="utf-8")
    (d / "node_modules").mkdir(exist_ok=True)
    (d / "node_modules" / "huge.js").write_text("x" * 100, encoding="utf-8")
    (d / ".factory").mkdir(exist_ok=True)
    (d / ".factory" / "history.json").write_text(json.dumps([
        {"ts": "2026-11-23T08:00:00", "action": "testrun", "detail": "red then green"},
        {"ts": "2026-11-23T08:01:00", "action": "generate", "detail": "ok"},
    ]), encoding="utf-8")
    if with_git:
        subprocess.run(["git", "init", "-q", "-b", "main", str(d)], check=True)
        subprocess.run(["git", "-C", str(d), "config", "user.email", "a@b.invalid"],
                       check=True)
        subprocess.run(["git", "-C", str(d), "config", "user.name", "AF Test"],
                       check=True)
        subprocess.run(["git", "-C", str(d), "add", "meta.json", "index.html",
                        "app.js", "requirements.txt"], check=True)
        subprocess.run(["git", "-C", str(d), "commit", "-qm", "fixture"], check=True)
    return d


class SnapshotTest(unittest.TestCase):
    def test_real_snapshot_excludes_secrets_and_bulky_paths(self):
        name = "af29-snapshot"
        _fixture(name)
        snap = project_snapshot.build(name)
        self.assertEqual(snap["status"], "ok")
        paths = snap["sections"]["files"]["paths"]
        self.assertIn("index.html", paths)
        self.assertIn("requirements.txt", paths)
        for bad in (".env", "product.key", "node_modules/huge.js",
                    ".factory/history.json"):
            self.assertNotIn(bad, paths, bad)
        self.assertEqual(snap["sections"]["dependencies"]["names"], ["json"])
        self.assertEqual(snap["sections"]["entry"]["path"], "index.html")
        self.assertEqual(snap["sections"]["recent_tests"]["status"], "ok")
        self.assertEqual(len(snap["sections"]["recent_tests"]["items"]), 1)

        prompt = project_snapshot.to_prompt(snap)
        self.assertIn("af29-snapshot", prompt)
        self.assertIn("index.html", prompt)
        self.assertNotIn("LEAKME", prompt)
        self.assertNotIn("PRIVATE-KEY", prompt)
        self.assertLessEqual(len(prompt), project_snapshot.MAX_PROMPT_CHARS)

    def test_real_git_status_from_fixture_repo(self):
        name = "af29-git"
        d = _fixture(name, with_git=True)
        snap = project_snapshot.build(name)
        git = snap["sections"]["git"]
        self.assertEqual(git["status"], "ok", git)
        self.assertTrue(git["head"])
        (d / "index.html").write_text("<html>changed</html>", encoding="utf-8")
        git2 = project_snapshot.build(name)["sections"]["git"]
        self.assertTrue(git2["dirty"])
        self.assertTrue(any("index.html" in c for c in git2["changed"]))


class ToolEventTest(unittest.TestCase):
    def _evt(self, tool, status, output=None, error=None, call="call-1"):
        # AF-30 — use the REAL OpenCode schema names, not AF-29's guesses.
        if status == "running":
            return {"id": "evt-" + call + "-called", "type": "session.tool.called",
                    "data": {"sessionID": "ses_af29", "id": call, "name": tool,
                             "input": {"cmd": "echo hi"}}}
        text = (output or "") + ("\nExited with code 1" if error else "")
        return {"id": "evt-" + call + "-success", "type": "session.tool.success",
                "data": {"sessionID": "ses_af29", "id": call, "name": tool,
                         "content": [{"type": "text", "text": text}],
                         "error": error}}

    def test_success_failure_and_missing_result_are_distinguished(self):
        ok = agent_events.record(self._evt("bash", "completed", "hello", call="c-ok"), "p", "ses_af29")
        bad = agent_events.record(self._evt("bash", "error", error="exit 1", call="c-bad"), "p", "ses_af29")
        empty = agent_events.record(self._evt("read", "running", call="c-empty"), "p", "ses_af29")
        self.assertEqual(ok["status"], opstatus.PASS)
        self.assertEqual(ok["output"], "hello")
        self.assertEqual(bad["status"], opstatus.FAIL)
        self.assertEqual(bad["error"], "exit 1")
        self.assertEqual(empty["status"], opstatus.NOT_TESTED)
        self.assertIn("نتیجهای ثبت نشد", empty["detail"])

    def test_permission_event_keeps_full_request(self):
        evt = {"id": "perm-1", "type": "permission.asked",
               "data": {"id": "perm-1", "sessionID": "ses_af29",
                        "action": "write", "resource": "/app/server.py",
                        "resources": ["/app/server.py", "/app/db.py"],
                        "save": ["*.py"],
                        "source": {"type": "tool", "messageID": "msg-1"},
                        "diff": "--- a\n+++ b\n+new"}}
        relay = opencode_bridge.permission_event_data(evt)
        for key in ("agent", "resources", "metadata", "diff"):
            self.assertIn(key, relay, key)
        self.assertEqual(relay["metadata"]["save"], ["*.py"])
        rec = agent_events.record(evt, "p", "ses_af29")
        self.assertEqual(rec["resources"], ["/app/server.py", "/app/db.py"])
        self.assertEqual(rec["metadata"]["source"]["type"], "tool")
        self.assertIn("+new", rec["diff"])

    def test_observed_failure_reaches_next_decision_prompt(self):
        name = "af29-decision"
        _fixture(name)
        agent_events.record(self._evt("bash", "error", error="UNIT-TEST-FAILURE"),
                            name, "ses_next")
        text = agent_events.decision_prompt(name, "ses_next")
        self.assertIn("FAIL", text)
        self.assertIn("UNIT-TEST-FAILURE", text)
        perception = factory.perception_block(name, "ses_next")
        self.assertIn("UNIT-TEST-FAILURE", perception)
        self.assertIn("af29-decision", perception)

    def test_permission_decision_stores_request_details(self):
        req = {"agent": "releaser", "resources": ["x.py"],
               "metadata": {"why": "unit"}, "diff": "+line"}
        rec = agent_events.record_permission_decision(
            "ses_af29", "perm-2", "reject", actor="panel", request=req)
        self.assertEqual(rec["agent"], "releaser")
        self.assertEqual(rec["resources"], ["x.py"])
        self.assertEqual(rec["metadata"]["request_metadata"]["why"], "unit")
        self.assertIn("+line", rec["diff"])


class ActionGateTest(unittest.TestCase):
    def test_empty_test_result_is_never_success(self):
        r = action_gate.gate_test({"ran": False, "ok": True, "note": "no coverage"})
        self.assertNotEqual(opstatus.overall(r["checks"]), opstatus.PASS)
        with self.assertRaises(action_gate.ActionDenied):
            action_gate.require(r["checks"], "test-gate")

    def test_real_factory_no_coverage_returns_ok_false(self):
        # REAL run: no test file and no runnable surface/coverage at all.
        name = "af29-nocov"
        d = _fixture(name)
        (d / "meta.json").write_text(json.dumps(
            {"name": name, "type": "custom-unknown", "version": "1.0.0"}),
            encoding="utf-8")
        (d / "index.html").unlink()
        (d / "app.js").unlink()
        rep = factory.testrun_combined(name)
        self.assertFalse(rep.get("ok"))
        self.assertFalse(rep.get("ran"))
        self.assertNotEqual(rep["verification"]["status"], opstatus.PASS)

    def test_git_push_requires_real_remote_proof_and_scope(self):
        fake = {"status": "ok", "committed": True, "commit_count": 1,
                "head_local": "abc", "head_before": "old", "pushed": True,
                "online_match": True, "remote_head": "abc"}
        self.assertEqual(action_gate.gate_git(fake)["status"], opstatus.PASS)
        not_proven = dict(fake, online_match=None, remote_head="")
        self.assertNotEqual(action_gate.gate_git(not_proven)["status"], opstatus.PASS)
        self.assertEqual(action_gate.gate_git(fake, root_risk=True)["status"],
                         opstatus.BLOCKED)

    def test_smart_release_does_not_call_git_when_preconditions_fail(self):
        # UNIT: git_sync is mocked; the GATE itself and its wiring are real.
        name = "af29-gate"
        _fixture(name)
        with mock.patch.object(sr.git_sync, "inspect", return_value={
                "repo": True, "inside_ai_factory_root": True, "behind": 0,
                "ahead": 0, "project_is_repo_root": False, "changed": [],
                "root": str(config.PROJECT_ROOT), "public": False}), \
             mock.patch.object(project_git, "ensure_ready", return_value={
                 "status": "ok", "connected": True, "created": False,
                 "note": "mocked connected repo"}), \
             mock.patch.object(sr.git_sync, "commit_and_push") as push:
            r = sr.run(name, downloads_dir=str(config.LOGS_DIR / "af29-dl"),
                       push=True, auto_git=True)
        self.assertEqual(push.call_count, 0, "dangerous git op ran without gate")
        self.assertNotEqual(r["status"], sr.OK)
        self.assertNotEqual(r["git_preconditions"]["status"], opstatus.PASS)


class BoundaryTest(unittest.TestCase):
    def test_clean_env_drops_live_secret_names(self):
        with mock.patch.dict(os.environ, {
                "ANTHROPIC_AUTH_TOKEN": "sk-SECRET-AF29",
                "OPENCODE_SERVER_PASSWORD": "pw-af29",
                "MY_API_KEY": "key-af29"}):
            env = boundary.clean_env({"PORT": "1234"})
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        self.assertNotIn("OPENCODE_SERVER_PASSWORD", env)
        self.assertNotIn("MY_API_KEY", env)
        self.assertIn("PORT", env)
        self.assertIn("PATH", env)
        self.assertEqual(boundary.assert_clean(env), [])
        with self.assertRaises(PermissionError):
            boundary.clean_env({"MY_API_KEY": "x"})

    def test_headless_permission_allows_only_in_project_edits(self):
        name = "af33-headless"
        _fixture(name)
        self.assertEqual(boundary.headless_permission("edit", "index.html", name), "once")
        self.assertEqual(boundary.headless_permission("write", "new.py", name), "once")
        self.assertEqual(boundary.headless_permission("shell", "python verify.py", name), "once")
        # AF-41 — read-only echo is safe; the old blanket rejection caused
        # OpenCode to abort legitimate test chains ("Step interrupted").
        self.assertEqual(boundary.headless_permission("shell", "echo hi", name), "once")
        self.assertEqual(boundary.headless_permission("shell", "rm -rf x", name), "reject")
        self.assertEqual(boundary.headless_permission("edit", "../config.py", name), "reject")
        self.assertEqual(boundary.headless_permission("edit", "/etc/passwd", name), "reject")

    def test_project_policy_is_created_once_and_never_overwritten(self):
        name = "af29-policy"
        _fixture(name)
        pol = boundary.ensure_project_policy(name)
        self.assertEqual(pol["status"], "created")
        p = boundary.project_policy_path(config.PROJECTS_DIR / name)
        self.assertEqual(json.loads(p.read_text())["permission"]["bash"], "ask")
        p.write_text('{"permission":{"bash":"allow"}}', encoding="utf-8")
        again = boundary.ensure_project_policy(name)
        self.assertEqual(again["status"], "incomplete")
        self.assertEqual(json.loads(p.read_text())["permission"]["bash"], "allow")


if __name__ == "__main__":
    unittest.main()
