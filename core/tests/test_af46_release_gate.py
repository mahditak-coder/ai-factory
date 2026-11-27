"""AF-46 — release gate must catch plaintext secrets in JSON (UNIT)."""
import json
import shutil
import unittest

import core.config as config
from core import security_gate


class ReleaseGateTest(unittest.TestCase):
    def test_json_password_literal_blocks_export(self):
        name = "af46-gate-secret"
        d = config.PROJECTS_DIR / name
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        try:
            (d / "meta.json").write_text(json.dumps({"name": name, "type": "webapp"}),
                                         encoding="utf-8")
            (d / "bootstrap.json").write_text(
                json.dumps({"password": "Af6N8xO8xdF8EO80i1Qe"}),
                encoding="utf-8")
            result = security_gate.scan(name, mode="export")
            kinds = [f.get("kind") for f in result["critical"]]
            self.assertTrue(result["blocked"], result)
            self.assertIn("secret-embedded-literal", kinds)
            self.assertTrue(any(f.get("file") == "bootstrap.json"
                                for f in result["critical"]))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_placeholder_password_is_only_a_warning(self):
        name = "af46-gate-placeholder"
        d = config.PROJECTS_DIR / name
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        try:
            (d / "meta.json").write_text(json.dumps({"name": name, "type": "webapp"}),
                                         encoding="utf-8")
            (d / "config.json").write_text(
                json.dumps({"password": "changeme-example"}),
                encoding="utf-8")
            result = security_gate.scan(name, mode="export")
            self.assertFalse(result["blocked"], result)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
