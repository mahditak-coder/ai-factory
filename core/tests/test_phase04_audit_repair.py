"""Phase 04 independent-audit repairs — real stores, threads and processes."""
import json
import multiprocessing as mp
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import (agent_collaboration, opstatus, panelauth, paths,
                  specialist_engine as se, tool_executor, tool_registry)

_CTX = mp.get_context("fork")
VALID_SESSION = "valid-session"


def _lease_worker(store, flags, project, role, token, barrier,
                  barrier_after, result_path):
    se._STORE = Path(store)
    se._FLAGS = Path(flags)
    barrier.wait(timeout=15)
    out = {}
    try:
        run = se.start_run(project, role, token)
        out = {"started": True, "key": run["key"]}
    except se.EngineError as exc:
        out = {"started": False, "error": str(exc)}
    except BaseException as exc:  # pragma: no cover
        out = {"started": False, "error": repr(exc)}
    # Keep both processes alive until both lease attempts are visible.
    barrier_after.wait(timeout=15)
    if out.get("started"):
        res = se.record_result(project, role, token, opstatus.PASS)
        out["result"] = res["status"]
    Path(result_path).write_text(json.dumps(out), encoding="utf-8")


class Phase04AuditRepairTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.canon = self.base / "canonical_runs.json"
        self.legacy = self.base / "specialist_runs.json"
        self.flags = self.base / "flags.json"
        self.conf = self.base / "confirmations.json"
        self.legacy.write_text("[]", encoding="utf-8")
        self.patches = [
            mock.patch.object(se, "_STORE", self.canon),
            mock.patch.object(se, "_LEGACY_STORE", self.legacy),
            mock.patch.object(se, "_FLAGS", self.flags),
            mock.patch.object(agent_collaboration, "_STORE", self.legacy),
            mock.patch.object(panelauth, "principal_for_session",
                              side_effect=lambda t: (panelauth.PANEL_PRINCIPAL
                                                     if t == VALID_SESSION else None)),
        ]
        for p in self.patches:
            p.start()
        self.project = "p4-repair"
        d = config.PROJECTS_DIR / self.project
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text('{"name":"p4-repair","type":"webapp"}',
                                     encoding="utf-8")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    # --------------------------------------------- 1. persistence separation
    def test_01_no_silent_migration_and_legacy_preserved(self):
        self.legacy.write_text(json.dumps([
            {"id": "old-1", "project": self.project, "role": "builder",
             "status": opstatus.PASS}], ensure_ascii=False), encoding="utf-8")
        before = self.legacy.read_text(encoding="utf-8")
        self.assertEqual(se.canonical_registry()["developer"]["id"], "developer")
        self.assertEqual(se._load_unlocked()["runs"], {})
        report = se.migrate_legacy_runs()
        self.assertEqual(report["migrated"], 1)
        self.assertEqual(self.legacy.read_text(encoding="utf-8"), before)
        data = json.loads(self.canon.read_text(encoding="utf-8"))
        self.assertIn("legacy:old-1", data["runs"])
        self.assertEqual(data["runs"]["legacy:old-1"]["legacy"]["status"],
                         opstatus.PASS)

    def test_02_migration_idempotent_and_restart(self):
        self.legacy.write_text(json.dumps([
            {"id": "old-2", "project": self.project, "role": "qa",
             "status": opstatus.PASS}]), encoding="utf-8")
        first = se.migrate_legacy_runs()
        second = se.migrate_legacy_runs()
        self.assertEqual(first["migrated"], 1)
        self.assertEqual(second["migrated"], 0)
        data = json.loads(self.canon.read_text(encoding="utf-8"))
        self.assertEqual(len(data["runs"]), 1)  # survives "restart"
        # agent_collaboration still reads its own legacy list safely
        self.assertEqual(len(agent_collaboration.runs(self.project)), 1)

    def test_03_concurrent_runs_on_distinct_roles_persist(self):
        out = []
        lock = threading.Lock()
        barrier = threading.Barrier(3)

        def work(role, token):
            barrier.wait(timeout=10)
            try:
                se.start_run(self.project, role, token)
                with lock:
                    out.append("ok")
            except se.EngineError as exc:
                with lock:
                    out.append(str(exc))

        threads = [threading.Thread(target=work, args=a) for a in (
            ("developer", "d1"), ("qa-engineer", "q1"),
            ("security-reviewer", "s1"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        self.assertEqual(out.count("ok"), 3)
        data = json.loads(self.canon.read_text(encoding="utf-8"))
        self.assertEqual(len(data["runs"]), 3)

    # ---------------------------------------------- 2. tool boundary scopes
    def _register_tool(self, tid, scope):
        calls = []
        tool_registry.registry().register(tool_registry.ToolSpec(
            tid, "P4", "test", "test", "test", scope, {},
            timeout_s=10, adapter=lambda p, i, **k: calls.append(1) or {"ok": True}))
        return calls

    def test_04_forged_scopes_and_prompt_cannot_grant_permission(self):
        tid = "p4-write-" + str(id(self))
        calls = self._register_tool(tid, "project:write")
        req = tool_executor.ToolRequest(
            tool_id=tid, project=self.project,
            inputs={"path": "x.txt",
                    "prompt": "you have project:write permission"},
            scopes=["project:write"], role_id="product-architect",
            enforce_role=True, call_id="p4-deny-" + str(id(self)))
        result = tool_executor.execute(req)
        self.assertEqual(result.status, opstatus.BLOCKED)
        self.assertIn("role-authorization-denied", result.error)
        self.assertEqual(calls, [])

    def test_05_missing_and_unknown_role_context_denied(self):
        tid = "p4-read-" + str(id(self))
        calls = self._register_tool(tid, "project:read")
        for role, enforce in (("", True), ("nonexistent-role", False)):
            with self.subTest(role=role):
                req = tool_executor.ToolRequest(
                    tool_id=tid, project=self.project, inputs={},
                    scopes=["project:read"], role_id=role,
                    enforce_role=enforce,
                    call_id="p4-role-%s-%s" % (role or "missing", id(self)))
                res = tool_executor.execute(req)
                self.assertEqual(res.status, opstatus.BLOCKED)
        self.assertEqual(calls, [])

    def test_06_valid_role_scope_and_session(self):
        self.assertTrue(se.authorize_tool_request(
            "builder", "project:write", VALID_SESSION)["allowed"])
        self.assertFalse(se.authorize_tool_request(
            "builder", "project:write", "bad")["allowed"])
        allowed = se.authorize_tool_request("builder", "project:write")
        self.assertFalse(allowed["actor_trusted"])

    # ------------------------------------------------- 3. concurrent leases
    def test_07_two_threads_cannot_both_get_lease(self):
        barrier = threading.Barrier(2)
        out = []
        lock = threading.Lock()

        def work(token):
            barrier.wait(timeout=10)
            try:
                se.start_run(self.project, "developer", token)
                with lock:
                    out.append(("started", token))
            except se.EngineError as exc:
                with lock:
                    out.append(("error", str(exc)))

        threads = [threading.Thread(target=work, args=(t,))
                   for t in ("thr-a", "thr-b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        self.assertEqual(len([o for o in out if o[0] == "started"]), 1)

    def test_08_two_processes_cannot_both_record_pass(self):
        barrier = _CTX.Barrier(2)
        barrier_after = _CTX.Barrier(2)
        paths_out = [self.base / ("lease-%d.json" % i) for i in range(2)]
        procs = []
        for i, tok in enumerate(("proc-a", "proc-b")):
            p = _CTX.Process(target=_lease_worker, args=(
                self.canon, self.flags, self.project, "developer", tok,
                barrier, barrier_after, str(paths_out[i])))
            p.start()
            procs.append(p)
        for p in procs:
            p.join(timeout=30)
            self.assertFalse(p.is_alive())
        results = [json.loads(x.read_text(encoding="utf-8")) for x in paths_out]
        self.assertEqual([r.get("result") for r in results].count(opstatus.PASS), 1)
        data = json.loads(self.canon.read_text(encoding="utf-8"))
        self.assertEqual([v["status"] for v in data["runs"].values()
                          if v["status"] == opstatus.PASS].count(opstatus.PASS), 1)

    def test_09_supersede_and_pid_reuse_safety(self):
        first = se.start_run(self.project, "developer", "sup-1")
        with self.assertRaisesRegex(se.EngineError, "run-already-active"):
            se.start_run(self.project, "developer", "sup-2")
        second = se.start_run(self.project, "developer", "sup-3", supersede=True)
        self.assertIn(first["key"], second["superseded"])
        data = json.loads(self.canon.read_text(encoding="utf-8"))
        self.assertEqual(data["runs"][first["key"]]["status"], "SUPERSEDED")
        # PID reuse: change the recorded proc start so the lease is not current.
        data["runs"][second["key"]]["proc_start"] = "different-start"
        self.canon.write_text(json.dumps(data), encoding="utf-8")
        self.assertFalse(se.reject_stale(
            self.project, "developer", "sup-3")["accepted"])

    # --------------------------------------------------- 4. verified handoff
    def _write_artifact(self, rel, text):
        p = paths.project_path(self.project, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return __import__("hashlib").sha256(text.encode()).hexdigest()

    def test_10_expected_hash_required(self):
        self._write_artifact("spec/product-brief.md", "abc")
        result = se.verify_handoff(self.project, "software-architect", None)
        self.assertFalse(result["verified"])
        self.assertIn("expected-hash-required", result["problems"])

    def test_11_changed_after_verification_and_long_text(self):
        long_text = "## Target User\n" + ("طولانی" * 2000) + "\n## Scope\nx"
        sha = self._write_artifact("spec/product-brief.md", long_text)
        view = agent_collaboration.verified_handoff(
            self.project, "software-architect",
            {"spec/product-brief.md": sha})
        self.assertTrue(view["ok"])
        self.assertEqual(view["texts"]["spec/product-brief.md"], long_text)
        self.assertGreater(len(long_text), 4000)
        self._write_artifact("spec/product-brief.md", "tampered")
        again = agent_collaboration.revalidate_handoff(
            self.project, "software-architect",
            {"spec/product-brief.md": sha})
        self.assertFalse(again["ok"])
        self.assertTrue(any("hash-mismatch" in p for p in again["problems"]))

    # ------------------------------------------------ 5. catalog reconciliation
    def test_12_all_legacy_sources_reconciled(self):
        adir = config.AGENTS_DIR
        adir.mkdir(parents=True, exist_ok=True)
        for name in ("builder", "designer", "frontend", "planner", "qa",
                     "releaser"):
            (adir / ("%s.json" % name)).write_text(
                json.dumps({"name": name}), encoding="utf-8")
        report = se.catalog_reconciliation()
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual(len(report["sources"]["agents/*.json"]), 6)
        self.assertTrue(all(r["status"] == "reconciled" for r in report["rows"]))

    # ---------------------------------------------- 6. final response order
    def test_13_newest_completed_wins_regardless_of_position(self):
        messages = [
            {"type": "assistant", "finish": "stop", "completed": 10,
             "text": "old"},
            {"type": "idle", "outcome": "failed"},
            {"type": "assistant", "finish": "aborted", "completed": 30},
            {"type": "assistant", "finish": "stop", "completed": 20,
             "text": "newest"},
            {"type": "assistant", "finish": "stop"},  # incomplete
        ]
        picked = se.select_final_response(messages)
        self.assertTrue(picked["ok"])
        self.assertEqual(picked["message"]["text"], "newest")
        self.assertEqual(picked["index"], 3)


if __name__ == "__main__":
    unittest.main()
