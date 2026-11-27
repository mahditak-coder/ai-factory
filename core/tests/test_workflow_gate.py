"""Human-gated workflow — persistence, approvals, questions, idempotency."""
import json, tempfile, threading, unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import opstatus, tool_events, tool_executor, workflow as wf


def fixture(name="wf-unit"):
    d = config.PROJECTS_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps({"name": name, "type": "webapp"}),
                                 encoding="utf-8")
    return d


def producer(path="spec/product-brief.md", text="## Target User\nx\n## Scope\ny\n"
                                                 "## Success Criteria\nz\n"):
    """A deterministic producer that still writes REAL files via the registry."""
    import secrets
    calls = []
    def run(project, stage, goal):
        r = tool_executor.execute(tool_executor.ToolRequest(
            tool_id="project.write_file", project=project,
            inputs={"path": path, "content": text}, scopes=["project:write"],
            task_id="wf-test", call_id="wf-prod-" + secrets.token_hex(8)))
        calls.append(r.call_id)
        if r.status != opstatus.PASS:
            return {"status": opstatus.FAIL, "error": r.error}
        return {"status": opstatus.PASS, "artifacts": [{
            "path": path, "sha256": (r.output or {}).get("sha256")}],
            "evidence": {"call_ids": list(calls)}}
    return run


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        fixture()
        self.tf = tempfile.NamedTemporaryFile(suffix=".json")
        Path(self.tf.name).write_text("{}", encoding="utf-8")
        self.tf2 = tempfile.NamedTemporaryFile(suffix=".json")
        Path(self.tf2.name).write_text("[]", encoding="utf-8")
        self.p1 = mock.patch.object(wf, "_STORE", Path(self.tf.name))
        self.p2 = mock.patch.object(tool_events, "_STORE", Path(self.tf2.name))
        self.p1.start(); self.p2.start()

    def tearDown(self):
        self.p1.stop(); self.p2.stop(); self.tf.close(); self.tf2.close()
        wf._ACTIVE.clear()

    def test_game_dag_has_13_stages_and_real_deps(self):
        r = wf.create("wf-unit", "webapp", "build a 2.5D game")
        self.assertEqual(len(r["stages"]), 13)
        ids = [s["id"] for s in r["stages"]]
        self.assertEqual(ids[0], "s01-product")
        self.assertEqual(ids[-1], "s13-release")
        self.assertIn("s09-audio", r["stages"][8]["id"])
        self.assertIn("s04-character", r["stages"][7]["deps"])
        self.assertEqual(r["status"], wf.RUNNING)

    def test_stage_cannot_run_without_approval(self):
        wf.create("wf-unit", "webapp", "web app")
        g = wf.guard("wf-unit", "s01-product")
        self.assertEqual(g["status"], "ok")
        r = wf.run_stage("wf-unit", "s01-product", "tok-1", producer())
        self.assertEqual(r["status"], opstatus.PASS)
        self.assertEqual(wf.state("wf-unit")["status"], wf.AWAIT_REVIEW)
        blocked = wf.guard("wf-unit", "s02-arch", "tok-2")
        self.assertEqual(blocked["status"], opstatus.BLOCKED)
        self.assertEqual(blocked["error"], "awaiting-user-review")

    def test_approve_runs_next_stage_exactly_once(self):
        wf.create("wf-unit", "webapp", "web app")
        wf.run_stage("wf-unit", "s01-product", "a1", producer())
        a = wf.approve("wf-unit", "s01-product", 1, "ap-1")
        self.assertEqual(a["status"], opstatus.PASS)
        self.assertEqual(a["next_stage"], "s02-arch")
        again = wf.approve("wf-unit", "s01-product", 1, "ap-2")
        self.assertEqual(again["status"], opstatus.BLOCKED)
        first = wf.run_stage("wf-unit", "s02-arch", "b1", producer(
            "spec/architecture.md", "## Decisions\nx\n## Components\ny\n## Test Strategy\nz\n"))
        self.assertEqual(first["status"], opstatus.PASS)
        idem = wf.run_stage("wf-unit", "s02-arch", "b1", producer(
            "spec/architecture.md", "## Decisions\nx\n## Components\ny\n## Test Strategy\nz\n"))
        self.assertTrue(idem.get("idempotent"))
        st = wf.state("wf-unit")
        self.assertEqual(len(next(s for s in st["stages"]
                                  if s["id"] == "s02-arch")["versions"]), 1)

    def test_reject_and_revise_keep_old_version(self):
        wf.create("wf-unit", "webapp", "web app")
        wf.run_stage("wf-unit", "s01-product", "r1", producer())
        self.assertEqual(wf.reject("wf-unit", "s01-product", "not good")["status"],
                         opstatus.PASS)
        self.assertEqual(wf.state("wf-unit")["status"], wf.REJECTED)
        self.assertEqual(wf.revise("wf-unit", "s01-product", "fix it")["status"],
                         opstatus.PASS)
        r = wf.run_stage("wf-unit", "s01-product", "r2", producer(
            text="## Target User\nv2\n## Scope\ny\n## Success Criteria\nz\n"))
        self.assertEqual(r["status"], opstatus.PASS)
        self.assertEqual(r["version"], 2)
        s = next(s for s in wf.state("wf-unit")["stages"] if s["id"] == "s01-product")
        self.assertEqual(len(s["versions"]), 2)

    def test_question_persists_and_blocks_deps(self):
        wf.create("wf-unit", "webapp", "web app")
        wf.run_stage("wf-unit", "s01-product", "q1", producer())
        wf.approve("wf-unit", "s01-product", 1, "ap-1")
        a = wf.ask("wf-unit", "s02-arch", "Which backend?", ["Python", "Node"],
                   True, "q-1")
        self.assertEqual(a["status"], opstatus.PASS)
        snap = wf.state("wf-unit")
        self.assertEqual(snap["status"], wf.AWAIT_ANSWER)
        self.assertEqual(snap["stages"][1]["question"]["id"], "q-1")
        self.assertEqual(wf.guard("wf-unit", "s02-arch", "q2")["error"],
                         "awaiting-user-answer")
        ans = wf.answer("wf-unit", "s02-arch", "Python", "q-1")
        self.assertEqual(ans["status"], opstatus.PASS)
        self.assertEqual(wf.state("wf-unit")["stages"][1]["answer"], "Python")

    def test_changed_artifact_invalidates_approval(self):
        wf.create("wf-unit", "webapp", "web app")
        wf.run_stage("wf-unit", "s01-product", "v1", producer())
        wf.approve("wf-unit", "s01-product", 1, "ap-1")
        p = config.PROJECTS_DIR / "wf-unit/spec/product-brief.md"
        p.write_text("CHANGED", encoding="utf-8")
        snap = wf.state("wf-unit")
        self.assertEqual(snap["status"], wf.RUNNING)
        s = snap["stages"][0]
        self.assertEqual(s["status"], wf.REVISION)
        self.assertEqual(s["approved_version"], 0)
        self.assertTrue(s["versions"][0].get("invalidated"))
        self.assertEqual(wf.approve("wf-unit", "s01-product", 1, "ap-2")["status"],
                         opstatus.BLOCKED)

    def test_concurrent_runs_execute_once(self):
        wf.create("wf-unit", "webapp", "web app")
        p = producer()
        results = []
        threads = [threading.Thread(target=lambda: results.append(
            wf.run_stage("wf-unit", "s01-product", "c-" + str(i), p)))
            for i in range(2)]
        for t in threads: t.start()
        for t in threads: t.join()
        oks = [r for r in results if r.get("status") == opstatus.PASS]
        self.assertEqual(len(oks), 1)
        self.assertTrue(any(r.get("error") == "stage-already-running"
                            for r in results))

    def test_handoff_marker_never_leaks_into_artifact(self):
        from core import agent_collaboration as ac
        raw = "===FILE spec/game-design.md (md)\n# سند\n## Mechanics\nx\n"
        self.assertFalse(ac._strip_fence(raw).startswith("===FILE"))
        self.assertIn("# سند", ac._strip_fence(raw))

    def test_retry_from_failure_keeps_versions(self):
        wf.create("wf-unit", "webapp", "web app")
        def bad(project, stage, goal):
            return {"status": opstatus.FAIL, "error": "real-failure"}
        self.assertEqual(wf.run_stage("wf-unit", "s01-product", "f1", bad)[
            "status"], opstatus.FAIL)
        self.assertEqual(wf.retry_failed("wf-unit", "s01-product", "retry")[
            "status"], opstatus.PASS)
        self.assertEqual(wf.state("wf-unit")["stages"][0]["status"], wf.REVISION)
        p = producer()
        r = wf.run_stage("wf-unit", "s01-product", "f2", p)
        self.assertEqual(r["version"], 1)
        self.assertEqual(len(wf.state("wf-unit")["stages"][0]["versions"]), 1)

    def test_amend_artifact_hash_keeps_version_and_history(self):
        wf.create("wf-unit", "webapp", "web app")
        wf.run_stage("wf-unit", "s01-product", "a1", producer())
        before = wf.state("wf-unit")["stages"][0]["versions"][0]["artifacts"][0]["sha256"]
        r = wf.amend_artifact_hash("wf-unit", "s01-product", 1,
            "spec/product-brief.md", "f" * 64, "formatting-only")
        self.assertEqual(r["status"], opstatus.PASS)
        st = wf.state("wf-unit")
        self.assertEqual(len(st["stages"][0]["versions"]), 1)
        self.assertEqual(st["stages"][0]["versions"][0]["artifacts"][0]["sha256"],
                         "f" * 64)
        self.assertTrue(any(h["action"] == "artifact-amended"
                            for h in st["history"]))
        self.assertNotEqual(before, "f" * 64)

    def test_revised_goal_persists_for_downstream(self):
        wf.create("wf-unit", "webapp", "old goal")
        self.assertEqual(wf.set_goal("wf-unit", "new real goal")["status"],
                         opstatus.PASS)
        st = wf.state("wf-unit")
        self.assertIn("note", st["stages"][0])
        self.assertEqual(wf.state("wf-unit")["goal"], "new real goal")
        self.assertTrue(any(h["action"] == "goal-updated"
                            for h in wf.state("wf-unit")["history"]))

    def test_revision_note_reaches_specialist_goal(self):
        from core import workflow_executor as ex
        from unittest import mock
        from core import agent_collaboration
        seen = {}
        def fake(project, role, goal, model=None):
            seen["goal"] = goal
            return {"status": opstatus.PASS, "artifacts": [
                {"path": "spec/x.md", "sha256": "abc", "status": opstatus.PASS}]}
        with mock.patch.object(agent_collaboration, "run_specialist", fake):
            ex.run("wf-unit", {"role": "software-architect", "kind": "document",
                               "note": "توضیح کامل اصلاح"}, "goal")
        self.assertIn("توضیح کامل اصلاح", seen["goal"])

    def test_tool_failure_is_not_pass(self):
        wf.create("wf-unit", "webapp", "web app")
        def bad(project, stage, goal):
            return {"status": opstatus.FAIL, "error": "real-tool-error"}
        r = wf.run_stage("wf-unit", "s01-product", "f1", bad)
        self.assertEqual(r["status"], opstatus.FAIL)
        self.assertEqual(wf.state("wf-unit")["status"], wf.FAILED)
        self.assertEqual(wf.state("wf-unit")["stages"][0]["status"], wf.FAILED)

    def test_normal_tools_need_no_extra_approval(self):
        wf.create("wf-unit", "webapp", "web app")
        r = producer()("wf-unit", {}, "")
        self.assertEqual(r["status"], opstatus.PASS)
        self.assertEqual(wf.state("wf-unit")["status"], wf.RUNNING)
        events = tool_events.events(project="wf-unit", limit=50)
        self.assertTrue(events)
        self.assertTrue(all(e.get("decision") in ("once", None) for e in events))


if __name__ == "__main__":
    unittest.main()
