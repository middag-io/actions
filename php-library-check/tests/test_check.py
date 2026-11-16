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
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def plan(**overrides):
    with tempfile.TemporaryDirectory() as tmp:
        plan_file, output = os.path.join(tmp, "plan.json"), os.path.join(tmp, "out")
        with redirect_stdout(io.StringIO()):
            status = check.command_plan(plan_args(plan_file=plan_file, github_output=output, **overrides))
        outputs = dict(line.split("=", 1) for line in Path(output).read_text().splitlines())
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
