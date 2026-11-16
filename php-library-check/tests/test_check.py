"""Tests of scripts/check.py: inputs, the plan and the run.

The run is tested with a fake subprocess.run: no PHP, no Composer.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import check  # noqa: E402

FIXTURE = HERE / "fixtures" / "basic"
REPOSITORY = HERE.parent.parent
COMMIT = "0123456789abcdef0123456789abcdef01234567"
EXPECTED = json.loads((HERE / "fixtures" / "expected" / "basic.json").read_text())
EXPECTED_HIGHEST = json.loads((HERE / "fixtures" / "expected" / "highest.json").read_text())
# Inputs of the highest dry-run job of _ci.yml that reach check.py.
HIGHEST_INPUTS = {
    "php_version": "8.4", "composer_validate": "false", "strict_psr": "false", "lint_script": "",
    "phpstan_script": "", "cs_fixer_script": "", "rector_script": "", "composer_scripts": "",
    "expect_majors": "symfony/console:8\nsymfony/process:8",
}


def plan_args(**overrides):
    values = {
        "workspace": str(FIXTURE), "path": ".", "php_version": "8.2", "static_checks": "true",
        "composer_validate": "true", "composer_validate_strict": "false", "strict_psr": "true",
        "lint_script": "lint", "phpstan_script": "check:stan", "cs_fixer_script": "check:style",
        "rector_script": "check:rector", "composer_scripts": "check:schemas", "test_script": "test",
        "expect_majors": "", "dry_run": "true", "plan_file": "", "github_output": "",
        "dependency_versions": "locked", "tool_cache_paths": "", "runner_os": "Linux", "commit": COMMIT,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def read_outputs(text):
    """Parses a GITHUB_OUTPUT file: name=value lines and name<<delimiter blocks."""
    outputs, lines = {}, iter(text.splitlines())
    for line in lines:
        if "<<" in line and "=" not in line.split("<<", 1)[0]:
            name, delimiter = line.split("<<", 1)
            block = []
            for value in lines:
                if value == delimiter:
                    break
                block.append(value)
            outputs[name] = "\n".join(block)
        else:
            name, value = line.split("=", 1)
            outputs[name] = value
    return outputs


def plan(**overrides):
    with tempfile.TemporaryDirectory() as tmp:
        plan_file, output = os.path.join(tmp, "plan.json"), os.path.join(tmp, "out")
        with redirect_stdout(io.StringIO()):
            status = check.command_plan(plan_args(plan_file=plan_file, github_output=output, **overrides))
        outputs = read_outputs(Path(output).read_text())
        record = json.loads(Path(plan_file).read_text())
        record["outputs"] = outputs
        return status, json.loads(outputs["commands"]), record


class InputTest(unittest.TestCase):
    def test_rejects_bad_values(self):
        cases = {
            "php-version": {"php_version": "8"},
            "static-checks": {"static_checks": "yes"},
            "composer-validate-strict": {"composer_validate_strict": "1"},
            "phpstan-script": {"phpstan_script": "check:stan; rm -rf /"},
            "composer-scripts": {"composer_scripts": "ok, --bad"},
            "test-script": {"test_script": "test\n"},
            "expect-majors": {"expect_majors": "symfony/console"},
        }
        for name, override in cases.items():
            with self.subTest(input=name), self.assertRaisesRegex(check.CheckError, name):
                check.parse_options(plan_args(**override))

    def test_expect_majors_takes_package_and_major_items(self):
        self.assertEqual(
            check.parse_expect_majors("symfony/console:8, symfony/http-kernel:8\npsr/log:3\n"),
            (("symfony/console", 8), ("symfony/http-kernel", 8), ("psr/log", 3)),
        )
        self.assertEqual(check.parse_expect_majors(""), ())

    def test_expect_majors_rejects_bad_items(self):
        for value in ("symfony/*:8", "Symfony/Console:8", "symfony/console:v8", "symfony/console:08",
                      "symfony/console:8.1", "symfony/console 8", "console:8", "symfony/console:8; rm -rf /"):
            with self.subTest(value=value), self.assertRaisesRegex(check.CheckError, "expect-majors"):
                check.parse_expect_majors(value)

    def test_expect_majors_takes_every_composer_name_form(self):
        for name in ("vendor/pkg", "a-b/c_d.e", "a.b_c-d/e--f", "a1/b2", "0/0", "vendor/a.b-c_d--e"):
            with self.subTest(name=name):
                self.assertEqual(check.parse_expect_majors(f"{name}:1"), ((name, 1),))

    def test_expect_majors_rejects_bad_separators(self):
        for name in ("-vendor/pkg", "vendor-/pkg", "ven--dor/pkg", "vendor/pkg---x", "vendor/-pkg",
                     "vendor/pkg-", "vendor/pkg.", "vendor/p..kg", "vendor/pkg.-x", "vendor/p_-kg"):
            with self.subTest(name=name), self.assertRaisesRegex(check.CheckError, "expect-majors"):
                check.parse_expect_majors(f"{name}:1")

    def test_expect_major_pattern_is_linear_on_adversarial_input(self):
        # A nested optional separator made this backtrack exponentially:
        # 24 characters already took about 0.4 s.
        for value in ("0" * 5000 + "!", "a/" + "0" * 5000 + "!", "a-" * 2500 + "/b:", "a/" + "b-" * 2500 + "!"):
            with self.subTest(length=len(value)):
                start = time.perf_counter()
                self.assertIsNone(check.EXPECT_MAJOR_RE.fullmatch(value))
                self.assertLess(time.perf_counter() - start, 0.5)

    def test_expect_majors_rejects_a_package_named_twice(self):
        with self.assertRaisesRegex(check.CheckError, "symfony/console twice"):
            check.parse_expect_majors("symfony/console:8, symfony/console:7")

    def test_path_stays_in_the_checkout_and_holds_composer_json(self):
        with self.assertRaisesRegex(check.CheckError, "leaves the checkout"):
            check.resolve_library_dir(FIXTURE, "..")
        with self.assertRaisesRegex(check.CheckError, "no composer.json"):
            check.resolve_library_dir(FIXTURE, "src")


class PlanTest(unittest.TestCase):
    def test_static_job_plans_the_expected_commands(self):
        status, commands, record = plan()
        self.assertEqual(status, 0)
        self.assertEqual(commands, EXPECTED["commands"])
        self.assertEqual(Path(record["directory"]), FIXTURE)

    def test_other_jobs_run_only_the_tests(self):
        _, commands, _ = plan(static_checks="false")
        self.assertEqual(commands, ["composer run-script --no-interaction test"])

    def test_empty_inputs_skip_their_step(self):
        _, commands, _ = plan(composer_validate="false", strict_psr="false", lint_script="", phpstan_script="",
                              cs_fixer_script="", rector_script="", composer_scripts="", test_script="")
        self.assertEqual(commands, [])

    def test_default_plans_no_expected_majors(self):
        _, _, record = plan()
        self.assertEqual(record["expect-majors"], {})
        self.assertEqual(record["outputs"]["expect-majors"], "{}")

    def test_highest_dry_run_matches_expected_ci_outputs(self):
        _, commands, record = plan(**HIGHEST_INPUTS)
        self.assertEqual(commands, EXPECTED_HIGHEST["commands"])
        self.assertEqual(json.loads(record["outputs"]["expect-majors"]), EXPECTED_HIGHEST["expect-majors"])

    def test_strict_validate_adds_the_flag(self):
        _, commands, _ = plan(composer_validate_strict="true")
        self.assertEqual(commands[0], "composer validate --no-interaction --no-check-publish --no-check-lock --strict")


class ToolCacheTest(unittest.TestCase):
    def test_no_tool_cache_by_default(self):
        _, _, record = plan()
        outputs = record["outputs"]
        self.assertEqual(outputs["tool-cache-paths"], "[]")
        for name in ("tool-cache-path-lines", "tool-cache-key", "tool-cache-restore-keys"):
            with self.subTest(output=name):
                self.assertEqual(outputs[name], "")

    def test_no_tool_cache_without_the_static_checks(self):
        _, _, record = plan(tool_cache_paths=".cache", static_checks="false")
        self.assertEqual(record["outputs"]["tool-cache-key"], "")
        self.assertEqual(record["outputs"]["tool-cache-paths"], "[]")

    def test_basic_dry_run_matches_expected_ci_outputs(self):
        _, _, record = plan(workspace=str(REPOSITORY), path="php-library-check/tests/fixtures/basic",
                            tool_cache_paths=".cache")
        self.assertEqual(json.loads(record["outputs"]["tool-cache-paths"]), EXPECTED["tool-cache-paths"])

    def test_plans_paths_key_and_restore_keys(self):
        _, _, record = plan(tool_cache_paths=".cache/phpstan\n.cache/rector, .php-cs-fixer.cache",
                            php_version="8.3", dependency_versions="highest")
        outputs = record["outputs"]
        self.assertEqual(json.loads(outputs["tool-cache-paths"]),
                         [".cache/phpstan", ".cache/rector", ".php-cs-fixer.cache"])
        self.assertEqual(outputs["tool-cache-path-lines"], ".cache/phpstan\n.cache/rector\n.php-cs-fixer.cache")
        key = outputs["tool-cache-key"]
        files_key, prefix = outputs["tool-cache-restore-keys"].split("\n")
        self.assertTrue(prefix.startswith("php-library-check-tools-Linux-php8.3-highest-"))
        self.assertTrue(files_key.startswith(prefix))
        self.assertEqual(key, files_key + COMMIT)
        self.assertNotIn(",", key)

    def test_key_changes_with_os_php_dependency_versions_path_and_commit(self):
        def key(**overrides):
            _, _, record = plan(tool_cache_paths=".cache", **overrides)
            return record["outputs"]["tool-cache-key"]

        base = key()
        for override in ({"runner_os": "macOS"}, {"php_version": "8.4"}, {"dependency_versions": "highest"},
                         {"commit": "f" * 40},
                         {"workspace": str(REPOSITORY), "path": "php-library-check/tests/fixtures/basic"}):
            with self.subTest(override=override):
                self.assertNotEqual(key(**override), base)

    def test_config_hash_follows_composer_and_tool_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp)
            (library / "composer.json").write_text("{}")
            first = check.tool_config_hash(library)
            (library / "src.php").write_text("<?php")
            self.assertEqual(check.tool_config_hash(library), first)
            for name in ("composer.lock", ".phpstan.neon", "phpstan.neon.dist", ".php-rector.php", "rector.php",
                         ".php-cs-fixer.dist.php", ".php-cs-fixer.php"):
                with self.subTest(file=name):
                    before = check.tool_config_hash(library)
                    (library / name).write_text(name)
                    after = check.tool_config_hash(library)
                    self.assertNotEqual(after, before)
                    (library / name).write_text(name + " changed")
                    self.assertNotEqual(check.tool_config_hash(library), after)

    def test_rejects_unsafe_paths(self):
        for value in ("/tmp/cache", "../cache", ".cache/../../x", ".", "./", "vendor", "vendor/bin", "*.cache",
                      ".cache/**", "!.cache", "~/.cache", "$HOME/.cache", ".cache\\x", "src/../vendor/x"):
            with self.subTest(value=value), self.assertRaisesRegex(check.CheckError, "tool-cache-paths"):
                plan(tool_cache_paths=value)

    def test_rejects_a_path_named_twice(self):
        with self.assertRaisesRegex(check.CheckError, "twice"):
            plan(tool_cache_paths=".cache, ./.cache")

    def test_rejects_bad_dependency_versions_runner_os_and_commit(self):
        cases = {"dependency-versions": {"dependency_versions": "lowest"}, "runner OS": {"runner_os": "Linux x"},
                 "commit": {"commit": "main"}}
        for message, override in cases.items():
            with self.subTest(case=message), self.assertRaisesRegex(check.CheckError, message):
                plan(tool_cache_paths=".cache", **override)

    def test_dry_run_prints_the_tool_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                check.command_plan(plan_args(plan_file=os.path.join(tmp, "plan.json"), tool_cache_paths=".cache"))
        self.assertIn("Would restore and save the tool caches .cache, key php-library-check-tools-Linux-php8.2-",
                      buffer.getvalue())

    def test_multi_line_outputs_use_the_delimiter_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "out")
            check.write_outputs(output, {"one": "a", "many": "b\nc"})
            self.assertEqual(read_outputs(Path(output).read_text()), {"one": "a", "many": "b\nc"})


class RunTest(unittest.TestCase):
    def test_runs_in_order_in_the_library_and_stops_at_the_first_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan_file = os.path.join(tmp, "plan.json")
            with redirect_stdout(io.StringIO()):
                check.command_plan(plan_args(plan_file=plan_file, dry_run="false"))
            calls = []

            def fake_run(command, cwd, env, check):
                calls.append((command, cwd))
                return subprocess.CompletedProcess(command, 2 if len(calls) == 3 else 0)

            buffer = io.StringIO()
            with mock.patch.object(check.subprocess, "run", fake_run), redirect_stdout(buffer):
                status = check.main(["run", "--plan-file", plan_file])
        self.assertEqual(status, 1)
        self.assertEqual(len(calls), 3)
        self.assertEqual(Path(calls[0][1]), FIXTURE)
        self.assertIn("::error::composer run-script --no-interaction lint failed with exit code 2", buffer.getvalue())


def show_output(**versions):
    installed = [{"name": name.replace("__", "/").replace("_", "-"), "version": version}
                 for name, version in versions.items()]
    return json.dumps({"installed": installed})


class MajorCheckTest(unittest.TestCase):
    def test_reports_missing_and_other_majors(self):
        installed = {"symfony/console": "v7.4.23", "symfony/process": "v8.1.11", "symfony/yaml": "8.1.x-dev"}
        expected = {"symfony/console": 8, "symfony/process": 8, "symfony/yaml": 8, "symfony/finder": 8}
        self.assertEqual(
            check.major_mismatches(expected, installed),
            [
                "symfony/console v7.4.23 is installed, expected major version 8",
                "symfony/finder is not installed (expected major version 8)",
            ],
        )

    def test_a_branch_without_a_version_is_a_mismatch(self):
        self.assertEqual(
            check.major_mismatches({"acme/lib": 1}, {"acme/lib": "dev-main"}),
            ["acme/lib dev-main is installed, expected major version 1"],
        )

    def run_plan(self, expect_majors, show_stdout, show_status=0):
        with tempfile.TemporaryDirectory() as tmp:
            plan_file = os.path.join(tmp, "plan.json")
            with redirect_stdout(io.StringIO()):
                check.command_plan(plan_args(plan_file=plan_file, dry_run="false", expect_majors=expect_majors,
                                             composer_validate="false", strict_psr="false", lint_script="",
                                             phpstan_script="", cs_fixer_script="", rector_script="",
                                             composer_scripts=""))
            calls, show_command = [], list(check.SHOW_COMMAND)

            def fake_run(command, cwd, check=False, env=None, capture_output=False, text=False):
                calls.append(command)
                if command == show_command:
                    return subprocess.CompletedProcess(command, show_status, stdout=show_stdout, stderr="")
                return subprocess.CompletedProcess(command, 0)

            buffer = io.StringIO()
            with mock.patch.object(check.subprocess, "run", fake_run), redirect_stdout(buffer):
                status = check.main(["run", "--plan-file", plan_file])
        return status, calls, buffer.getvalue()

    def test_a_mismatch_fails_before_any_check(self):
        status, calls, printed = self.run_plan("symfony/console:8", show_output(symfony__console="v7.4.23"))
        self.assertEqual(status, 1)
        self.assertEqual(calls, [list(check.SHOW_COMMAND)])
        self.assertIn("::error::expect-majors: symfony/console v7.4.23 is installed, expected major version 8", printed)

    def test_matching_majors_run_the_checks(self):
        status, calls, printed = self.run_plan("symfony/console:8", show_output(symfony__console="v8.1.11"))
        self.assertEqual(status, 0)
        self.assertEqual(calls, [list(check.SHOW_COMMAND), ["composer", "run-script", "--no-interaction", "test"]])
        self.assertIn("symfony/console v8.1.11: major version 8 as expected.", printed)

    def test_failed_composer_show_fails_the_job(self):
        status, calls, printed = self.run_plan("symfony/console:8", "", show_status=1)
        self.assertEqual(status, 1)
        self.assertEqual(len(calls), 1)
        self.assertIn("composer show --format=json --no-interaction failed with exit code 1", printed)

    def test_no_installed_package_is_a_mismatch(self):
        status, calls, printed = self.run_plan("symfony/console:8", "[]")
        self.assertEqual(status, 1)
        self.assertEqual(len(calls), 1)
        self.assertIn("symfony/console is not installed (expected major version 8)", printed)

    def test_output_that_is_not_json_fails_the_job(self):
        status, _, printed = self.run_plan("symfony/console:8", "Warning: something")
        self.assertEqual(status, 1)
        self.assertIn("did not print JSON", printed)

    def test_no_expected_majors_runs_no_composer_show(self):
        status, calls, _ = self.run_plan("", "")
        self.assertEqual(status, 0)
        self.assertEqual(calls, [["composer", "run-script", "--no-interaction", "test"]])


if __name__ == "__main__":
    unittest.main()
