"""AF-47 — release blocker closure (UNIT); real functions, isolated stores."""
import json
import shutil
import unittest
from pathlib import Path
from unittest import mock

import core.config as config
from core import (agents, boundary, opencode_bridge, runner, security_gate,
                  typetests)


def _fixture(name: str, php: bool = False) -> Path:
    d = config.PROJECTS_DIR / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"name": name, "type":
        "wordpress" if php else "webapp"}), encoding="utf-8")
    if php:
        (d / "index.php").write_text('<?php echo "ok"; ?>\n', encoding="utf-8")
    return d


class SecurityRemediationTest(unittest.TestCase):
    def test_remediation_names_files_and_secure_bootstrap(self):
        g = {"critical": [{"file": "data/admin_bootstrap.json",
                           "kind": "secret-embedded-literal"}]}
        text = security_gate.remediation(g)
        self.assertIn("admin_bootstrap.json", text)
        self.assertIn("متغیر محیطی", text)
        self.assertIn("یکبارمصرف", text)
        self.assertIn("تحویل مسدود", text)


class PhpFirstClassTest(unittest.TestCase):
    def test_types_exist_in_real_registry(self):
        self.assertIn("wordpress", config.PROJECT_TYPE_SPECS)
        self.assertIn("php", config.PROJECT_TYPE_SPECS)
        self.assertIn("wordpress", config.PROJECT_TYPES)

    def test_runner_detects_php_and_lints(self):
        name = "af47-runner-php"
        d = _fixture(name, php=True)
        try:
            det = runner._detect(d)
            self.assertEqual(det["kind"], "php")
            rep = runner.run_tests(name)
            self.assertTrue(rep["ran"], rep)
            self.assertTrue(rep["ok"], rep)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_php_shell_only_allows_lint(self):
        name = "af47-boundary-php"
        d = _fixture(name, php=True)
        try:
            self.assertEqual(boundary.headless_permission(
                "shell", ["php -l index.php"], name), "once")
            self.assertEqual(boundary.headless_permission(
                "shell", ["php -r 'echo 1;'"], name), "reject")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_wordpress_contract_detects_real_defects(self):
        name = "af47-wp-contract"
        d = _fixture(name, php=True)
        try:
            errs = []
            typetests.check_wordpress(d, errs)
            kinds = {e["kind"] for e in errs}
            self.assertIn("wp-header", kinds)
            self.assertIn("wp-shortcode", kinds)
            self.assertIn("wp-woocommerce", kinds)
            (d / "plugin.php").write_text(
                '<?php\n/* Plugin Name: Real */\nadd_shortcode("x", "f");\n'
                'add_action("init", "f");\nadd_options_page("s","s",'
                '"manage_options","s","f");\n// woocommerce hook\n',
                encoding="utf-8")
            errs2 = []
            typetests.check_wordpress(d, errs2)
            self.assertEqual(errs2, [], errs2)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class QuotaDetectionTest(unittest.TestCase):
    def test_real_quota_message_detected(self):
        self.assertTrue(opencode_bridge.is_quota_error(
            "Free usage limit reached. retry_after_ms=27985000"))
        self.assertFalse(opencode_bridge.is_quota_error("Step interrupted"))

    def test_no_fallback_without_verified_models(self):
        with mock.patch.object(opencode_bridge, "models", return_value=[]):
            self.assertEqual(agents.alternative_model("builder"), "")

    def test_fallback_uses_only_real_enabled_model(self):
        models = [{"providerID": "opencode", "modelID": "big-pickle",
                   "active": True, "enabled": True}]
        with mock.patch.object(opencode_bridge, "models", return_value=models), \
             mock.patch.object(agents, "get_agent",
                               return_value={"model": "opencode/deepseek-free"}):
            self.assertEqual(agents.alternative_model("builder"),
                             "opencode/big-pickle")


if __name__ == "__main__":
    unittest.main()
