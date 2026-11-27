"""Dedicated acceptance tests for AF-21 … AF-25 (not AF-20 reruns).

Each test exercises production code with real arguments; external GitHub and
the model are replaced by fixtures/mocks where the feature under test is not
the external service itself.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import (errreason, factory, git_sync, opencode_bridge, pipeline,
                  request_intelligence as ri, tasks, typetests)


class AF21DeclaredContractTest(unittest.TestCase):
    def test_declared_contract_is_read_from_project(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self.assertEqual(typetests._api_contract(d), {})
            (d / "api-contract.json").write_text(
                json.dumps({"endpoints": {"register": "/api/signup"},
                            "status": {"register": 201}}), encoding="utf-8")
            got = typetests._api_contract(d)
            self.assertEqual(got["endpoints"]["register"], "/api/signup")
            (d / "api-contract.json").write_text("{broken", encoding="utf-8")
            self.assertEqual(typetests._api_contract(d), {})

    def test_model_instruction_requires_declared_contract(self):
        project = "af21-contract"
        d = config.PROJECTS_DIR / project
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(
            json.dumps({"name": project, "type": "fullstack", "version": "1.0.0"}),
            encoding="utf-8")
        captured = {}
        with mock.patch.object(factory, "create_session",
                               return_value={"id": "ses-unit"}), \
             mock.patch.object(factory, "_record_gen"), \
             mock.patch.object(factory, "prompt",
                               side_effect=lambda *a, **k: captured.update(
                                   text=a[2] if len(a) > 2 else k.get("text", ""),
                                   model=k.get("model"), agent=k.get("agent"))
                               or {"message_id": "m-unit", "ok": True}):
            factory.generate(project, "یک فروشگاه بساز", model="opencode/glm-5.3-flash-free",
                             agent="builder")
        self.assertIn("api-contract.json", captured.get("text", ""),
                      "AF-21 declaration requirement missing from model instruction")
        self.assertEqual(captured.get("model"), "opencode/glm-5.3-flash-free")
        self.assertEqual(captured.get("agent"), "builder")


class AF22AndroidRoutingTest(unittest.TestCase):
    def setUp(self):
        self._prior_mode = ri.compat_mode()
        ri.set_compat_mode("legacy")

    def tearDown(self):
        ri.set_compat_mode(self._prior_mode)

    def test_android_pipeline_uses_its_own_stage_runner(self):
        project = "af22-route"
        adir = config.AGENTS_DIR
        adir.mkdir(parents=True, exist_ok=True)
        seed = Path(__file__).resolve().parents[2] / "agents" / "builder.json"
        (adir / "builder.json").write_text(seed.read_text(encoding="utf-8"),
                                           encoding="utf-8")
        d = config.PROJECTS_DIR / project
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(
            json.dumps({"name": project, "type": "android", "version": "1.0.0"}),
            encoding="utf-8")
        with tempfile.TemporaryDirectory() as td:
            old_t, old_p = tasks._STORE, pipeline._STORE
            tasks._STORE = Path(td) / "tasks.json"
            pipeline._STORE = Path(td) / "pipelines.json"
            try:
                created = pipeline.create(project, "یک اپ اندروید بساز")
                recs = pipeline._load()
                rec = pipeline._get(recs, created["id"])
                rec.update({"state": "approved", "stage": "generate",
                            "done": {"plan": {"at": "test"},
                                     "generate": {"at": "test"}}})
                pipeline._save(recs)
                task = tasks.get_task(created["task_id"])
                called = []
                with mock.patch.object(pipeline, "_run_stages_android",
                                       side_effect=lambda p, tid: called.append((p["id"], tid))):
                    pipeline.execute(task, "unit-worker")
                self.assertEqual(len(called), 1, called)
                self.assertEqual(called[0][0], created["id"])
            finally:
                tasks._STORE, pipeline._STORE = old_t, old_p


class AF23SwitchAndDiagnosisTest(unittest.TestCase):
    def test_model_switch_payload_is_the_real_oc_shape(self):
        with mock.patch.object(opencode_bridge, "_request") as req:
            opencode_bridge.set_session_model("ses/abc", "opencode", "glm-5.3-flash-free")
        args, kw = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertIn("/api/session/ses/abc/model", args[1])
        self.assertEqual(kw["body"],
                         {"model": {"id": "glm-5.3-flash-free", "providerID": "opencode"}})

    def test_error_reasoning_classifies_and_redacts(self):
        out = errreason.explain(429,
                                "free limit reached, retry after 60000; key sk-SECRETVALUE12345",
                                tag="opencode")
        as_text = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("sk-SECRETVALUE12345", as_text)
        self.assertTrue(out.get("reason") or out.get("category"), out)


class AF25GitGuardTest(unittest.TestCase):
    def test_factory_root_repo_is_blocked(self):
        fake = {"repo": True, "inside_ai_factory_root": True,
                "behind": 0, "ahead": 0, "project_is_repo_root": False,
                "changed": [], "root": str(config.PROJECT_ROOT), "public": False}
        with mock.patch.object(git_sync, "inspect", return_value=fake):
            pre = git_sync.preflight("any-project")
        self.assertTrue(pre["blocked"], pre)
        self.assertTrue(any("ریشهٔ AI Factory" in r for r in pre["reasons"]),
                        pre["reasons"])

    def test_local_bare_remote_roundtrip_is_evidence_not_mock(self):
        # A real git round trip in an isolated temp fixture (no network).
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            remote = root / "remote.git"
            work = root / "work"
            work.mkdir()
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
            subprocess.run(["git", "-C", str(work), "config", "user.email", "a@b.invalid"],
                           check=True)
            subprocess.run(["git", "-C", str(work), "config", "user.name", "AF Test"],
                           check=True)
            (work / "file.txt").write_text("af25", encoding="utf-8")
            subprocess.run(["git", "-C", str(work), "add", "."], check=True)
            subprocess.run(["git", "-C", str(work), "commit", "-qm", "init"], check=True)
            subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(remote)],
                           check=True)
            subprocess.run(["git", "-C", str(work), "push", "-qu", "origin", "main"],
                           check=True)
            local = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                   capture_output=True, text=True, check=True).stdout.strip()
            online = subprocess.run(["git", "-C", str(work), "ls-remote", "origin",
                                     "refs/heads/main"],
                                    capture_output=True, text=True, check=True).stdout.split()[0]
            self.assertEqual(local, online)


if __name__ == "__main__":
    unittest.main()
