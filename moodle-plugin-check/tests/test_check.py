"""Tests of scripts/check.py, with the fixture plugin and temporary copies."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "check.py"
FIXTURE = HERE / "fixtures" / "basic"
EXPECTED = HERE / "fixtures" / "expected" / "basic.json"

spec = importlib.util.spec_from_file_location("check", SCRIPT)
check = importlib.util.module_from_spec(spec)
sys.modules["check"] = check
spec.loader.exec_module(check)


def plan_args(workspace, plan_file, output, **overrides):
    values = {
        "path": "plugin",
        "component": "local_example",
        "php-version": "8.3",
        "php-extensions": "intl, zip",
        "composer-validate": "true",
        "composer-options": "--prefer-dist --optimize-autoloader",
        "composer-scripts": "check:style, test",
        "app-client-id": "",
        "app-repositories": "",
        "has-app-private-key": "false",
        "dry-run": "true",
    }
    values.update(overrides)
    args = ["plan", f"--workspace={workspace}", f"--plan-file={plan_file}", f"--github-output={output}"]
    args += [f"--{key}={value}" for key, value in values.items()]
    return args


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.workspace = self.tmp / "workspace"
        shutil.copytree(FIXTURE, self.workspace / "plugin")
        self.plan_file = self.tmp / "plan.json"
        self.output = self.tmp / "output"

    def run_plan(self, **overrides):
        return check.main(plan_args(self.workspace, self.plan_file, self.output, **overrides))

    def outputs(self):
        lines = self.output.read_text(encoding="utf-8").splitlines()
        return dict(line.split("=", 1) for line in lines)

    def write(self, relative, text):
        target = self.workspace / "plugin" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def test_fixture_matches_the_expected_plan(self):
        self.assertEqual(self.run_plan(), 0)
        outputs = self.outputs()
        expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
        self.assertEqual(outputs["component"], expected["component"])
        self.assertEqual(outputs["version"], expected["version"])
        self.assertEqual(json.loads(outputs["commands"]), expected["commands"])
        self.assertEqual(outputs["release"], "1.2.0")
        self.assertEqual(outputs["maturity"], "MATURITY_STABLE")

    def test_plan_file_holds_argument_lists_and_the_plugin_directory(self):
        self.run_plan()
        plan = json.loads(self.plan_file.read_text(encoding="utf-8"))
        self.assertEqual(Path(plan["directory"]), (self.workspace / "plugin").resolve())
        self.assertEqual(plan["commands"][-1], ["composer", "run-script", "test"])

    def test_without_composer_json_only_metadata_is_checked(self):
        (self.workspace / "plugin" / "composer.json").unlink()
        self.assertEqual(self.run_plan(**{"composer-scripts": ""}), 0)
        self.assertEqual(json.loads(self.outputs()["commands"]), [])

    def test_scripts_without_composer_json_fail(self):
        (self.workspace / "plugin" / "composer.json").unlink()
        self.assertEqual(self.run_plan(), 1)

    def test_composer_validate_false_drops_the_validate_command(self):
        self.run_plan(**{"composer-validate": "false"})
        commands = json.loads(self.outputs()["commands"])
        self.assertNotIn("composer validate --no-interaction", commands)

    def test_scripts_split_on_new_lines(self):
        self.run_plan(**{"composer-scripts": "test\ncheck:stan\n"})
        commands = json.loads(self.outputs()["commands"])
        self.assertEqual(commands[-2:], ["composer run-script test", "composer run-script check:stan"])

    def test_component_mismatch_fails(self):
        self.assertEqual(self.run_plan(component="local_other"), 1)

    def test_empty_component_skips_the_comparison(self):
        self.assertEqual(self.run_plan(component=""), 0)

    def test_invalid_inputs_fail(self):
        cases = {
            "php-version": "8",
            "php-extensions": "intl; rm -rf /",
            "component": "Local-Example",
            "composer-options": "--prefer-dist; echo",
            "composer-scripts": "test; echo",
            "composer-validate": "yes",
            "dry-run": "maybe",
        }
        for key, value in cases.items():
            with self.subTest(input=key):
                self.assertEqual(self.run_plan(**{key: value}), 1)

    def test_short_composer_option_fails(self):
        self.assertEqual(self.run_plan(**{"composer-options": "-o"}), 1)

    def test_path_outside_the_checkout_fails(self):
        for path in ("../plugin", "/tmp", "", "missing"):
            with self.subTest(path=path):
                self.assertEqual(self.run_plan(path=path), 1)

    def test_app_client_id_without_key_fails_outside_a_dry_run(self):
        overrides = {"app-client-id": "Iv1.example", "app-repositories": "library", "dry-run": "false"}
        self.assertEqual(self.run_plan(**overrides), 1)

    def test_app_client_id_without_key_passes_in_a_dry_run(self):
        self.assertEqual(self.run_plan(**{"app-client-id": "Iv1.example", "app-repositories": "library"}), 0)

    def test_app_client_id_with_key_passes(self):
        overrides = {
            "app-client-id": "Iv1.example",
            "app-repositories": "library-one, library-two",
            "has-app-private-key": "true",
            "dry-run": "false",
        }
        self.assertEqual(self.run_plan(**overrides), 0)

    def test_app_client_id_without_repositories_fails(self):
        overrides = {"app-client-id": "Iv1.example", "has-app-private-key": "true", "dry-run": "false"}
        self.assertEqual(self.run_plan(**overrides), 1)

    def test_repository_with_owner_fails(self):
        self.assertEqual(self.run_plan(**{"app-client-id": "Iv1.example", "app-repositories": "owner/library"}), 1)

    def test_missing_version_php_fails(self):
        (self.workspace / "plugin" / "version.php").unlink()
        self.assertEqual(self.run_plan(), 1)

    def test_version_php_errors_fail(self):
        base = "<?php\n$plugin->component = 'local_example';\n"
        cases = {
            "no version": base,
            "short version": base + "$plugin->version = 20261103;\n",
            "invalid date": base + "$plugin->version = 2026023000;\n",
            "bad maturity": base + "$plugin->version = 2026110300;\n$plugin->maturity = MATURITY_GOOD;\n",
            "bad requires": base + "$plugin->version = 2026110300;\n$plugin->requires = 45;\n",
            "twice": base + "$plugin->version = 2026110300;\n$plugin->version = 2026110301;\n",
            "not literal": base + "$plugin->version = get_version();\n",
            "no php tag": "$plugin->component = 'local_example';\n$plugin->version = 2026110300;\n",
        }
        for name, source in cases.items():
            with self.subTest(case=name):
                self.write("version.php", source)
                self.assertEqual(self.run_plan(), 1)

    def test_commented_assignment_is_ignored(self):
        self.write(
            "version.php",
            "<?php\n$plugin->component = 'local_example';\n"
            "// $plugin->version = 1;\n/* $plugin->version = 2; */\n$plugin->version = 2026110300.01;\n",
        )
        self.assertEqual(self.run_plan(), 0)
        self.assertEqual(self.outputs()["version"], "2026110300.01")

    def test_fields_the_check_does_not_read_may_hold_any_expression(self):
        self.write(
            "version.php",
            "<?php\n$plugin->component = 'local_example';\n$plugin->version = 2026110300;\n"
            "$plugin->supported = [405, 502];\n"
            "$plugin->dependencies = ['mod_forum' => ANY_VERSION];\n"
            "$plugin->incompatible = 400;\n$plugin->incompatible = 401;\n",
        )
        self.assertEqual(self.run_plan(), 0)

    def test_missing_language_file_fails(self):
        (self.workspace / "plugin" / "lang" / "en" / "local_example.php").unlink()
        self.assertEqual(self.run_plan(), 1)


class LanguageFileTest(unittest.TestCase):
    def test_activity_modules_use_the_plugin_name(self):
        self.assertEqual(check.language_file("mod_forum"), "lang/en/forum.php")

    def test_other_types_use_the_component(self):
        self.assertEqual(check.language_file("local_example"), "lang/en/local_example.php")
        self.assertEqual(check.language_file("assignfeedback_example"), "lang/en/assignfeedback_example.php")


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.plan_file = self.tmp / "plan.json"

    def write_plan(self, commands):
        self.plan_file.write_text(json.dumps({"directory": str(self.tmp), "commands": commands}), encoding="utf-8")

    def args(self):
        return check.parser().parse_args(["run", f"--plan-file={self.plan_file}"])

    def test_runs_commands_in_order_without_a_shell_and_stops_at_the_first_failure(self):
        self.write_plan([["composer", "validate"], ["composer", "install"], ["composer", "run-script", "test"]])
        calls = []

        def runner(command, cwd, env, check):
            calls.append(command)
            return subprocess.CompletedProcess(command, 1 if command[1] == "install" else 0)

        with self.assertRaises(check.CheckError):
            check.command_run(self.args(), runner=runner)
        self.assertEqual(calls, [["composer", "validate"], ["composer", "install"]])

    def test_runs_in_the_plugin_directory(self):
        self.write_plan([["composer", "validate"]])
        seen = {}

        def runner(command, cwd, env, check):
            seen["cwd"] = cwd
            return subprocess.CompletedProcess(command, 0)

        self.assertEqual(check.command_run(self.args(), runner=runner), 0)
        self.assertEqual(seen["cwd"], str(self.tmp))

    def test_real_run_of_a_plan(self):
        self.write_plan([[sys.executable, "-c", "pass"]])
        self.assertEqual(check.command_run(self.args()), 0)


class ComposerEnvTest(unittest.TestCase):
    def test_token_becomes_composer_auth_and_leaves_the_environment(self):
        env = check.composer_env({"PATH": "/bin", "COMPOSER_GITHUB_TOKEN": "ghs_example"})
        self.assertNotIn("COMPOSER_GITHUB_TOKEN", env)
        self.assertEqual(json.loads(env["COMPOSER_AUTH"]), {"github-oauth": {"github.com": "ghs_example"}})
        self.assertEqual(env["PATH"], "/bin")

    def test_no_token_no_composer_auth(self):
        env = check.composer_env({"PATH": "/bin", "COMPOSER_GITHUB_TOKEN": ""})
        self.assertNotIn("COMPOSER_AUTH", env)


class ComparePlanTest(unittest.TestCase):
    def compare(self, **env):
        full = {**os.environ, **env}
        return subprocess.run(
            [sys.executable, "-I", "-B", str(HERE / "compare_plan.py"), str(EXPECTED)],
            env=full,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_expected_outputs_pass(self):
        expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
        result = self.compare(
            COMPONENT=expected["component"], VERSION=expected["version"], COMMANDS=json.dumps(expected["commands"])
        )
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_other_outputs_fail(self):
        result = self.compare(COMPONENT="local_example", VERSION="2026110300", COMMANDS="[]")
        self.assertEqual(result.returncode, 1)

    def test_invalid_commands_fail(self):
        result = self.compare(COMPONENT="local_example", VERSION="2026110300", COMMANDS="")
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
