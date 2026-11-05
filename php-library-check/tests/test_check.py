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
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import check  # noqa: E402

FIXTURE = HERE / "fixtures" / "basic"
EXPECTED = json.loads((HERE / "fixtures" / "expected" / "basic.json").read_text())


def plan_args(**overrides):
    values = {
        "workspace": str(FIXTURE), "path": ".", "php_version": "8.2", "static_checks": "true",
        "composer_validate": "true", "composer_validate_strict": "false", "strict_psr": "true",
        "lint_script": "lint", "phpstan_script": "check:stan", "cs_fixer_script": "check:style",
        "rector_script": "check:rector", "composer_scripts": "check:schemas", "test_script": "test",
        "dry_run": "true", "plan_file": "", "github_output": "",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def plan(**overrides):
    with tempfile.TemporaryDirectory() as tmp:
        plan_file, output = os.path.join(tmp, "plan.json"), os.path.join(tmp, "out")
        with redirect_stdout(io.StringIO()):
            status = check.command_plan(plan_args(plan_file=plan_file, github_output=output, **overrides))
        outputs = dict(line.split("=", 1) for line in Path(output).read_text().splitlines())
        return status, json.loads(outputs["commands"]), json.loads(Path(plan_file).read_text())


class InputTest(unittest.TestCase):
    def test_rejects_bad_values(self):
        cases = {
            "php-version": {"php_version": "8"},
            "static-checks": {"static_checks": "yes"},
            "composer-validate-strict": {"composer_validate_strict": "1"},
            "phpstan-script": {"phpstan_script": "check:stan; rm -rf /"},
            "composer-scripts": {"composer_scripts": "ok, --bad"},
            "test-script": {"test_script": "test\n"},
        }
        for name, override in cases.items():
            with self.subTest(input=name), self.assertRaisesRegex(check.CheckError, name):
                check.parse_options(plan_args(**override))

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


if __name__ == "__main__":
    unittest.main()
