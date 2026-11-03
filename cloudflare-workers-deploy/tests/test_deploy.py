"""Tests of scripts/deploy.py: input rules, the plan and the Wrangler call.

Wrangler is never run: the deploy tests replace the process with a fake that
prints a fixed output. No network, no credential.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "scripts", "deploy.py")
FIXTURE = os.path.join(HERE, "fixtures", "basic")
VERSION_ID = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"

spec = importlib.util.spec_from_file_location("deploy", SCRIPT)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


def plan_args(workspace, **overrides):
    values = {
        "working-directory": "project",
        "node-version": "22",
        "build-script": "build",
        "wrangler-config": "",
        "wrangler-environment": "production",
        "worker-name": "",
        "account-id": "",
        "dry-run": "true",
        "has-api-token": "false",
        "out-dir": os.path.join(workspace, "out"),
    }
    values.update(overrides)
    return ["plan", *(f"--{key}={value}" for key, value in values.items())]


class FakeProcess:
    def __init__(self, lines, code):
        self.stdout = iter(lines)
        self.code = code

    def wait(self):
        return self.code


class Workspace(unittest.TestCase):
    """A temporary workspace with a copy of the fixture under project/."""

    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.workspace)
        self.project = os.path.join(self.workspace, "project")
        shutil.copytree(FIXTURE, self.project)
        self.output = os.path.join(self.workspace, "github-output")
        patcher = mock.patch.dict(
            os.environ, {"GITHUB_WORKSPACE": self.workspace, "GITHUB_OUTPUT": self.output}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, argv):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = deploy.main(argv)
        return code, stdout.getvalue()

    def outputs(self):
        with open(self.output, encoding="utf-8") as handle:
            return dict(line.rstrip("\n").split("=", 1) for line in handle if "=" in line)

    def edit_package(self, change):
        path = os.path.join(self.project, "package.json")
        with open(path, encoding="utf-8") as handle:
            package = json.load(handle)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(change(package), handle)


class PlanTest(Workspace):
    def test_dry_run_plan_of_the_fixture(self):
        code, out = self.run_main(plan_args(self.workspace))
        self.assertEqual(code, 0, out)
        outputs = self.outputs()
        self.assertEqual(outputs["wrangler-config"], "wrangler.toml")
        self.assertEqual(outputs["working-directory"], "project")
        self.assertEqual(outputs["build-script"], "build")
        self.assertIn("npm exec --no -- wrangler deploy --config wrangler.toml --env production", out)
        with open(outputs["plan-file"], encoding="utf-8") as handle:
            self.assertTrue(json.load(handle)["dry-run"])

    def test_config_detection_prefers_jsonc(self):
        with open(os.path.join(self.project, "wrangler.jsonc"), "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        code, _ = self.run_main(plan_args(self.workspace))
        self.assertEqual(code, 0)
        self.assertEqual(self.outputs()["wrangler-config"], "wrangler.jsonc")

    def test_worker_name_and_account_id_reach_the_plan(self):
        code, out = self.run_main(
            plan_args(self.workspace, **{"worker-name": "example-worker-2", "account-id": "0" * 32})
        )
        self.assertEqual(code, 0, out)
        self.assertIn("--name example-worker-2", out)
        self.assertIn("account-id: from input", out)

    def test_deployment_needs_the_api_token(self):
        code, out = self.run_main(plan_args(self.workspace, **{"dry-run": "false"}))
        self.assertEqual(code, 1)
        self.assertIn("input api-token is empty", out)

    def test_deployment_plan_with_the_api_token(self):
        code, out = self.run_main(plan_args(self.workspace, **{"dry-run": "false", "has-api-token": "true"}))
        self.assertEqual(code, 0, out)

    def test_rejected_inputs(self):
        cases = {
            "working-directory": ["/abs", "../up", "project/../..", "missing", ""],
            "node-version": ["", "22; rm", "latest"],
            "build-script": ["build && curl", "-x", "missing-script"],
            "wrangler-config": ["../wrangler.toml", "/etc/passwd", "wrangler.yaml", "other.toml"],
            "wrangler-environment": ["prod env", "-x", "a" * 65],
            "worker-name": ["Upper", "-lead", "trail-", "a" * 64],
            "account-id": ["ABC", "0" * 31, "g" * 32],
            "dry-run": ["yes", ""],
        }
        for name, values in cases.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    code, out = self.run_main(plan_args(self.workspace, **{name: value}))
                    self.assertEqual(code, 1, out)
                    self.assertIn("::error::", out)

    def test_link_out_of_the_workspace_is_rejected(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside)
        os.symlink(outside, os.path.join(self.workspace, "link"))
        code, out = self.run_main(plan_args(self.workspace, **{"working-directory": "link"}))
        self.assertEqual(code, 1)
        self.assertIn("resolves outside", out)

    def test_project_without_lockfile(self):
        os.remove(os.path.join(self.project, "package-lock.json"))
        code, out = self.run_main(plan_args(self.workspace))
        self.assertEqual(code, 1)
        self.assertIn("package-lock.json", out)

    def test_project_without_wrangler_dependency(self):
        self.edit_package(lambda package: {**package, "devDependencies": {}})
        code, out = self.run_main(plan_args(self.workspace))
        self.assertEqual(code, 1)
        self.assertIn("does not declare wrangler", out)

    def test_project_without_config(self):
        os.remove(os.path.join(self.project, "wrangler.toml"))
        code, out = self.run_main(plan_args(self.workspace))
        self.assertEqual(code, 1)
        self.assertIn("set the input wrangler-config", out)

    def test_empty_build_script_skips_the_build(self):
        code, out = self.run_main(plan_args(self.workspace, **{"build-script": ""}))
        self.assertEqual(code, 0, out)
        self.assertIn("build: (none)", out)


class DeployTest(Workspace):
    def write_plan(self, **overrides):
        plan = {
            "working-directory": "project",
            "node-version": "22",
            "build-script": "build",
            "wrangler-config": "wrangler.toml",
            "wrangler-environment": "production",
            "worker-name": "",
            "account-id": "",
            "dry-run": False,
        }
        plan.update(overrides)
        path = os.path.join(self.workspace, "plan.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(plan, handle)
        return path

    def deploy_with(self, plan_file, lines, code=0, token="token-value"):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return FakeProcess(lines, code)

        args = deploy.parser().parse_args(["deploy", f"--plan-file={plan_file}"])
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": token}), contextlib.redirect_stdout(stdout):
            try:
                deploy.command_deploy(args, runner)
                error = None
            except deploy.InputError as raised:
                error = str(raised)
        return calls, stdout.getvalue(), error

    def test_runs_the_project_wrangler_and_reads_the_version_id(self):
        plan_file = self.write_plan(**{"account-id": "f" * 32, "worker-name": "example-worker"})
        calls, out, error = self.deploy_with(plan_file, ["Uploaded\n", f"Current Version ID: {VERSION_ID}\n"])
        self.assertIsNone(error)
        command, kwargs = calls[0]
        self.assertEqual(
            command,
            ["npm", "exec", "--no", "--", "wrangler", "deploy", "--config", "wrangler.toml",
             "--env", "production", "--name", "example-worker"],
        )
        self.assertEqual(kwargs["cwd"], "project")
        self.assertEqual(kwargs["env"]["CLOUDFLARE_ACCOUNT_ID"], "f" * 32)
        self.assertEqual(self.outputs()["version-id"], VERSION_ID)
        self.assertIn("Uploaded", out)
        self.assertNotIn("token-value", out)

    def test_without_account_id_the_variable_is_not_set(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLOUDFLARE_ACCOUNT_ID", None)
            calls, _, error = self.deploy_with(self.write_plan(), [])
        self.assertIsNone(error)
        self.assertNotIn("CLOUDFLARE_ACCOUNT_ID", calls[0][1]["env"])

    def test_wrangler_failure_fails_the_step(self):
        _, _, error = self.deploy_with(self.write_plan(), ["boom\n"], code=1)
        self.assertIn("exited with status 1", error)

    def test_missing_version_id_warns(self):
        _, out, error = self.deploy_with(self.write_plan(), ["done\n"])
        self.assertIsNone(error)
        self.assertIn("::warning::", out)
        self.assertEqual(self.outputs()["version-id"], "")

    def test_empty_token_is_refused_before_wrangler(self):
        calls, _, error = self.deploy_with(self.write_plan(), [], token="")
        self.assertIn("CLOUDFLARE_API_TOKEN is empty", error)
        self.assertEqual(calls, [])

    def test_dry_run_plan_is_never_deployed(self):
        calls, _, error = self.deploy_with(self.write_plan(**{"dry-run": True}), [])
        self.assertIn("refusing to deploy", error)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
