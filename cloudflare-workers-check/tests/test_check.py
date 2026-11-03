"""Tests of scripts/check.py. Run: python3 -I -B -m unittest discover -s cloudflare-workers-check/tests"""

from __future__ import annotations

import contextlib
import importlib.util
import io
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
COMPARE = HERE / "compare_plan.py"

spec = importlib.util.spec_from_file_location("cloudflare_workers_check", SCRIPT)
check = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = check
spec.loader.exec_module(check)


class ProjectCase(unittest.TestCase):
    """Copies the fixture into a temporary workspace for each test."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name) / "workspace"
        shutil.copytree(FIXTURE, self.workspace / "app")
        self.plan_file = Path(self.tmp.name) / "plan.json"
        self.output_file = Path(self.tmp.name) / "output"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def plan(self, **overrides: str) -> tuple[int, str]:
        values = {
            "path": "app",
            "node-version": "22",
            "npm-scripts": "build",
            "npm-scripts-if-present": "typecheck, lint",
            "wrangler-config": "",
            "wrangler-dry-run": "false",
            "dry-run": "true",
        }
        values.update({key.replace("_", "-"): value for key, value in overrides.items()})
        argv = ["plan", f"--workspace={self.workspace}", f"--plan-file={self.plan_file}", f"--github-output={self.output_file}"]
        argv += [f"--{key}={value}" for key, value in values.items()]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check.main(argv)
        return code, out.getvalue()

    def outputs(self) -> dict[str, str]:
        lines = self.output_file.read_text(encoding="utf-8").splitlines()
        return dict(line.split("=", 1) for line in lines)

    def package(self) -> Path:
        return self.workspace / "app" / "package.json"

    def write_package(self, data: object) -> None:
        self.package().write_text(json.dumps(data), encoding="utf-8")


class PlanTest(ProjectCase):
    def test_plans_install_present_scripts_and_required_scripts_in_order(self) -> None:
        code, out = self.plan()
        self.assertEqual(code, 0, out)
        self.assertEqual(
            json.loads(self.outputs()["commands"]),
            ["npm ci", "npm run lint", "npm run build"],
        )
        self.assertIn("Skipped npm script typecheck", out)
        self.assertIn("Would run: npm ci", out)

    def test_plans_wrangler_dry_run_with_found_config(self) -> None:
        code, out = self.plan(wrangler_dry_run="true")
        self.assertEqual(code, 0, out)
        commands = json.loads(self.outputs()["commands"])
        self.assertEqual(commands[-1], "npx --no-install wrangler deploy --dry-run --config wrangler.toml")
        self.assertEqual(self.outputs()["wrangler-config"], "wrangler.toml")
        self.assertEqual(self.outputs()["name"], "example-worker")

    def test_uses_explicit_wrangler_config(self) -> None:
        (self.workspace / "app" / "config").mkdir()
        shutil.move(self.workspace / "app" / "wrangler.toml", self.workspace / "app" / "config" / "prod.toml")
        code, out = self.plan(wrangler_config="config/prod.toml", wrangler_dry_run="true")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.outputs()["wrangler-config"], "config/prod.toml")

    def test_prefers_json_config_over_toml(self) -> None:
        (self.workspace / "app" / "wrangler.jsonc").write_text("{}", encoding="utf-8")
        code, out = self.plan()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.outputs()["wrangler-config"], "wrangler.jsonc")

    def test_writes_plan_file_with_directory_and_argument_lists(self) -> None:
        self.plan()
        plan = json.loads(self.plan_file.read_text(encoding="utf-8"))
        self.assertEqual(Path(plan["directory"]), (self.workspace / "app").resolve())
        self.assertEqual(plan["commands"][0], ["npm", "ci"])

    def test_dry_run_matches_expected_reference_plan(self) -> None:
        code, out = self.plan(npm_scripts="build", npm_scripts_if_present="typecheck, lint", wrangler_dry_run="true")
        self.assertEqual(code, 0, out)
        outputs = self.outputs()
        env = dict(os.environ, NAME=outputs["name"], WRANGLER_CONFIG=outputs["wrangler-config"], COMMANDS=outputs["commands"])
        result = subprocess.run([sys.executable, "-I", "-B", str(COMPARE), str(EXPECTED)], env=env, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class RejectTest(ProjectCase):
    def assert_fails(self, message: str, **overrides: str) -> None:
        code, out = self.plan(**overrides)
        self.assertEqual(code, 1, out)
        self.assertIn("::error::", out)
        self.assertIn(message, out)

    def test_rejects_bad_node_version(self) -> None:
        self.assert_fails("input node-version", node_version="22; rm -rf /")

    def test_rejects_script_name_with_shell_syntax(self) -> None:
        self.assert_fails("input npm-scripts:", npm_scripts="build && curl x")

    def test_rejects_script_in_both_lists(self) -> None:
        self.assert_fails("in both npm-scripts and npm-scripts-if-present", npm_scripts="lint")

    def test_rejects_missing_required_script(self) -> None:
        self.assert_fails("package.json has no script 'test'", npm_scripts="build, test")

    def test_rejects_path_outside_checkout(self) -> None:
        self.assert_fails("leaves the checkout", path="../..")

    def test_rejects_absolute_path(self) -> None:
        self.assert_fails("must be a relative path", path="/etc")

    def test_rejects_wrangler_config_outside_project(self) -> None:
        self.assert_fails("input wrangler-config", wrangler_config="../../outside.toml")

    def test_rejects_missing_wrangler_config_file(self) -> None:
        self.assert_fails("is not a file of the project", wrangler_config="missing.toml")

    def test_rejects_project_without_wrangler_config(self) -> None:
        (self.workspace / "app" / "wrangler.toml").unlink()
        self.assert_fails("no Wrangler configuration")

    def test_rejects_missing_package_json(self) -> None:
        self.package().unlink()
        self.assert_fails("package.json is missing")

    def test_rejects_missing_lock_file(self) -> None:
        (self.workspace / "app" / "package-lock.json").unlink()
        self.assert_fails("package-lock.json is missing")

    def test_rejects_invalid_package_json(self) -> None:
        self.package().write_text("{", encoding="utf-8")
        self.assert_fails("package.json is not valid JSON")

    def test_rejects_package_json_that_is_not_an_object(self) -> None:
        self.write_package([])
        self.assert_fails("does not hold a JSON object")

    def test_rejects_scripts_that_are_not_an_object(self) -> None:
        self.write_package({"scripts": []})
        self.assert_fails("scripts is not an object")

    def test_rejects_wrangler_dry_run_without_wrangler_dependency(self) -> None:
        self.write_package({"name": "x", "scripts": {"build": "true"}})
        self.assert_fails("wrangler is not in the dependencies", wrangler_dry_run="true")

    def test_rejects_non_boolean_flags(self) -> None:
        self.assert_fails("input wrangler-dry-run must be true or false", wrangler_dry_run="yes")
        self.assert_fails("input dry-run must be true or false", dry_run="1")


class RunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.plan_file = Path(self.tmp.name) / "plan.json"
        self.plan_file.write_text(
            json.dumps({"directory": self.tmp.name, "commands": [["npm", "ci"], ["npm", "run", "build"]]}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_plan(self, returncodes: list[int]) -> tuple[int, list[list[str]], str]:
        calls: list[list[str]] = []

        def runner(command, cwd, check):
            calls.append(command)
            self.assertEqual(cwd, self.tmp.name)
            self.assertFalse(check)
            return subprocess.CompletedProcess(command, returncodes[len(calls) - 1])

        args = check.parser().parse_args(["run", f"--plan-file={self.plan_file}"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                code = check.command_run(args, runner=runner)
            except check.CheckError as error:
                return 1, calls, str(error)
        return code, calls, out.getvalue()

    def test_runs_every_command_in_order(self) -> None:
        code, calls, _ = self.run_plan([0, 0])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [["npm", "ci"], ["npm", "run", "build"]])

    def test_stops_at_first_failure(self) -> None:
        code, calls, message = self.run_plan([2, 0])
        self.assertEqual(code, 1)
        self.assertEqual(calls, [["npm", "ci"]])
        self.assertIn("npm ci failed with exit code 2", message)


if __name__ == "__main__":
    unittest.main()
