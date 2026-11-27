"""Core behaviours with real inputs: boundaries, invalid data, recovery.

Every assertion calls production code with concrete values; no grep/AST-only
checks and no 'exit code 0 means success' shortcuts.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import confirmations, deps, memory, paths, release


class PathSafetyTest(unittest.TestCase):
    def test_project_names_boundaries(self):
        self.assertTrue(paths.project_name_valid("abc-123"))
        self.assertTrue(paths.project_name_valid("معلم من"))
        self.assertFalse(paths.project_name_valid(""))
        self.assertFalse(paths.project_name_valid("../etc"))
        self.assertFalse(paths.project_name_valid("x" * 65))
        self.assertFalse(paths.project_name_valid("a/b"))

    def test_resolve_safe_blocks_escape_and_absolute(self):
        base = config.PROJECTS_DIR
        for bad in ("../config.py", "/etc/passwd", "a/../../b"):
            with self.subTest(bad=bad), self.assertRaises(PermissionError):
                paths.resolve_safe(base, bad)
        self.assertEqual(paths.resolve_safe(base, "proj/file.txt"),
                         (base / "proj/file.txt").resolve())


class MemoryBudgetTest(unittest.TestCase):
    def test_context_is_bounded_and_filters_secrets(self):
        project = "af26-mem"
        ns = "project:" + project
        memory.set_value(ns, "api_key", "ghp_" + "A" * 30)
        memory.set_value(ns, "note", "safe " * 500)
        ctx = memory.build_context(project)
        self.assertIsInstance(ctx, str)
        self.assertNotIn("A" * 30, ctx)
        self.assertLessEqual(len(ctx), 2000)


class DependencyProbeTest(unittest.TestCase):
    def test_importable_true_and_false(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "goodmod.py").write_text("VALUE = 1\n", encoding="utf-8")
            self.assertTrue(deps.importable("goodmod", p))
            self.assertFalse(deps.importable("no_such_module_af26", p))

    def test_requirements_status_three_states(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "requirements.txt").write_text("json\nno_such_pkg_af26==1.2.3\n",
                                                encoding="utf-8")
            st = deps.requirements_status(p)
            self.assertIn("json", st)
            missing = [k for k in st if k.startswith("no_such_pkg_af26")]
            self.assertTrue(missing, st)
            self.assertTrue(st["json"]["installed"])
            self.assertFalse(st[missing[0]]["usable"])


class ConfirmationStoreTest(unittest.TestCase):
    def test_single_use_project_binding_expiry(self):
        c = confirmations.create("proj-a", "create-github-repo",
                                 {"private": True}, "sوال؟", "sess-1")
        token = c["confirm_token"]
        self.assertEqual(confirmations.peek(token, "proj-a")["action"],
                         "create-github-repo")
        with self.assertRaises(confirmations.ConfirmError):
            confirmations.peek(token, "proj-b")
        with self.assertRaises(confirmations.ConfirmError):
            confirmations.peek(token, "proj-a", "different-session")
        used = confirmations.consume(token, "proj-a", "sess-1")
        self.assertEqual(used["project"], "proj-a")
        with self.assertRaises(confirmations.ConfirmError):
            confirmations.consume(token, "proj-a", "sess-1")

    def test_expired_token_is_rejected(self):
        c = confirmations.create("proj-e", "x", {}, "s?", "s")
        items = confirmations._load()
        items["items"][c["confirm_token"]]["expires"] = 1
        confirmations._save(items)
        with self.assertRaises(confirmations.ConfirmError) as ctx:
            confirmations.peek(c["confirm_token"])
        self.assertEqual(ctx.exception.status, 410)


class ReleaseVerificationTest(unittest.TestCase):
    def test_manifest_tamper_is_detected(self):
        import hashlib
        import zipfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            proj = "af26proj"
            stage = p / proj
            stage.mkdir()
            files = {
                "release.json": json.dumps({
                    "name": proj, "type": "webpage", "version": "1.0.0",
                    "technology": {"kind": "static-web", "deps": [],
                                   "entry": "app.py"}}),
                "README.md": "readme",
                "run.sh": "#!/bin/sh\nexit 0\n",
                "run.bat": "@echo off\n",
                "app.py": "print('hi')\n",
                "index.html": "<html></html>\n",
            }
            for name, content in files.items():
                (stage / name).write_text(content, encoding="utf-8")
            lines = ["%s  %s" % (hashlib.sha256((stage / n).read_bytes()).hexdigest(), n)
                     for n in sorted(files)]
            (stage / "MANIFEST.sha256").write_text("\n".join(lines) + "\n",
                                                   encoding="utf-8")
            zpath = p / "rel.zip"
            with zipfile.ZipFile(zpath, "w") as zf:
                for f in sorted(stage.rglob("*")):
                    zf.write(f, "%s/%s" % (proj, f.relative_to(stage).as_posix()))
            ok = release.verify_release(zpath, proj)
            self.assertTrue(ok.get("ok"), ok)

            # Tamper with a packed file and rebuild the ZIP with the old manifest.
            (stage / "app.py").write_text("print('TAMPERED')\n", encoding="utf-8")
            with zipfile.ZipFile(zpath, "w") as zf:
                for f in sorted(stage.rglob("*")):
                    zf.write(f, "%s/%s" % (proj, f.relative_to(stage).as_posix()))
            bad = release.verify_release(zpath, proj)
            self.assertFalse(bad.get("ok"), bad)
            self.assertFalse(bad["checks"]["release_manifest"], bad)
            self.assertTrue(bad["manifest"]["problems"], bad)


class IsolationTest(unittest.TestCase):
    def test_root_isolated_when_env_set(self):
        self.assertEqual(config.PROJECT_ROOT,
                         Path(config._TEST_ROOT).resolve() if config._TEST_ROOT
                         else config.PROJECT_ROOT)
        if config._TEST_ROOT:
            self.assertEqual(config.PROJECT_ROOT, Path(config._TEST_ROOT).resolve())
            self.assertTrue(config.PROJECT_ROOT.is_dir())
        else:
            self.assertEqual(config.PROJECT_ROOT, Path(__file__).resolve().parents[2])


if __name__ == "__main__":
    unittest.main()
