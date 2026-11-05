"""Tests of scripts/host.py: input validation, the plan, config.php and the run.

The run is tested with a fake subprocess.run: no clone, no PHP, no database.

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

import host  # noqa: E402

FIXTURE = HERE / "fixtures" / "basic"
EXPECTED = HERE / "fixtures" / "expected" / "basic.json"
REPOSITORY_ROOT = HERE.parent.parent


def plan_args(**overrides):
    values = {
        "workspace": str(REPOSITORY_ROOT),
        "moodle_branch": "MOODLE_405_STABLE",
        "moodle_repository": "moodle/moodle",
        "php_version": "8.3",
        "php_extensions": "pgsql, intl",
        "path": "moodle-host-check/tests/fixtures/basic",
        "probe_script": "moodle-host-check/tests/fixtures/basic/probe.php",
        "db_host": "127.0.0.1",
        "db_port": "5432",
        "db_name": "moodle",
        "db_user": "postgres",
        "dry_run": "true",
        "plan_file": "",
        "github_output": "",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def run_plan(**overrides):
    with tempfile.TemporaryDirectory() as tmp:
        plan_file = os.path.join(tmp, "plan.json")
        output = os.path.join(tmp, "output")
        args = plan_args(plan_file=plan_file, github_output=output, **overrides)
        with redirect_stdout(io.StringIO()):
            status = host.run_plan(args)
        outputs = dict(line.split("=", 1) for line in Path(output).read_text().splitlines())
        return status, outputs, json.loads(Path(plan_file).read_text())


class InputTest(unittest.TestCase):
    def test_rejects_a_branch_that_is_not_a_moodle_branch(self):
        for branch in ("", "MOODLE_405", "v4.5.0", "main; rm -rf /", "MOODLE_405_STABLE\n"):
            with self.subTest(branch=branch), self.assertRaisesRegex(host.HostError, "moodle-branch"):
                host.parse_options(plan_args(moodle_branch=branch))

    def test_accepts_stable_branches_and_main(self):
        for branch in ("MOODLE_405_STABLE", "MOODLE_500_STABLE", "main"):
            with self.subTest(branch=branch):
                self.assertEqual(host.parse_options(plan_args(moodle_branch=branch)).moodle_branch, branch)

    def test_rejects_a_repository_that_is_not_owner_name(self):
        for repository in ("moodle", "https://github.com/moodle/moodle", "a/b/c"):
            with self.subTest(repository=repository), self.assertRaisesRegex(host.HostError, "moodle-repository"):
                host.parse_options(plan_args(moodle_repository=repository))

    def test_rejects_bad_php_version_port_and_names(self):
        cases = {
            "php-version": {"php_version": "8"},
            "db-port": {"db_port": "70000"},
            "db-host": {"db_host": "host name"},
            "db-name": {"db_name": "moodle;"},
            "db-user": {"db_user": "1user"},
            "php-extensions": {"php_extensions": "intl; curl"},
        }
        for name, override in cases.items():
            with self.subTest(input=name), self.assertRaisesRegex(host.HostError, name):
                host.parse_options(plan_args(**override))

    def test_rejects_a_path_outside_the_checkout_or_without_composer_json(self):
        with self.assertRaisesRegex(host.HostError, "leaves the checkout"):
            host.resolve_code_dir(REPOSITORY_ROOT, "../..")
        with self.assertRaisesRegex(host.HostError, "no composer.json"):
            host.resolve_code_dir(REPOSITORY_ROOT, "moodle-host-check")

    def test_rejects_a_probe_that_is_not_a_php_file_of_the_checkout(self):
        for probe in ("/etc/passwd", "../probe.php", "moodle-host-check/README.md", "missing.php"):
            with self.subTest(probe=probe), self.assertRaises(host.HostError):
                host.resolve_probe(REPOSITORY_ROOT, probe)

    def test_empty_probe_is_the_builtin_probe(self):
        self.assertEqual(host.resolve_probe(REPOSITORY_ROOT, ""), host.BUILTIN_PROBE)
        self.assertTrue(host.BUILTIN_PROBE.is_file())


class PlanTest(unittest.TestCase):
    def test_plan_of_the_fixture_matches_the_expected_commands(self):
        status, outputs, _ = run_plan()
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(outputs["commands"]), json.loads(EXPECTED.read_text()))

    def test_builtin_probe_is_shown_by_a_placeholder(self):
        _, outputs, _ = run_plan(probe_script="")
        self.assertEqual(json.loads(outputs["commands"])[-1], "php -d max_input_vars=5000 <builtin-probe>")

    def test_plan_file_holds_the_options_and_absolute_directories(self):
        _, _, record = run_plan()
        self.assertEqual(record["moodle_branch"], "MOODLE_405_STABLE")
        self.assertEqual(Path(record["code_dir"]), FIXTURE)
        self.assertEqual(Path(record["probe"]), FIXTURE / "probe.php")

    def test_bad_input_fails_with_an_annotation(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            status = host.main(["plan", "--workspace", str(REPOSITORY_ROOT), "--moodle-branch", "x"])
        self.assertEqual(status, 1)
        self.assertIn("::error::input moodle-branch", buffer.getvalue())


class ConfigTest(unittest.TestCase):
    def test_config_php_uses_postgresql_without_password(self):
        options = host.parse_options(plan_args())
        text = host.config_php(options, Path("/tmp/data's"))
        self.assertIn("$CFG->dbtype = 'pgsql';", text)
        self.assertIn("$CFG->dbpass = '';", text)
        self.assertIn("'dbport' => 5432", text)
        self.assertIn("$CFG->dataroot = '/tmp/data\\'s';", text)
        self.assertTrue(text.rstrip().endswith("require_once(__DIR__ . '/lib/setup.php');"))

    def test_composer_install_only_when_moodle_requires_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            moodle = Path(tmp)
            self.assertFalse(host.needs_composer_install(moodle))
            (moodle / "composer.json").write_text(json.dumps({"require": {"php": ">=8.1", "ext-intl": "*"}}))
            self.assertFalse(host.needs_composer_install(moodle))
            (moodle / "composer.json").write_text(json.dumps({"require": {"php": ">=8.3", "slim/slim": "4.15.2"}}))
            self.assertTrue(host.needs_composer_install(moodle))
            (moodle / "composer.json").write_text("[")
            with self.assertRaises(host.HostError):
                host.needs_composer_install(moodle)


class RunTest(unittest.TestCase):
    def run_host(self, returncodes):
        calls = []

        def fake_run(command, cwd, env, check):
            calls.append((command, Path(cwd), env))
            if command[:2] == ["git", "clone"]:
                Path(command[-1]).mkdir(parents=True)
            return subprocess.CompletedProcess(command, returncodes.pop(0) if returncodes else 0)

        with tempfile.TemporaryDirectory() as tmp:
            plan_file = os.path.join(tmp, "plan.json")
            with redirect_stdout(io.StringIO()):
                host.run_plan(plan_args(plan_file=plan_file, dry_run="false"))
            work = Path(tmp) / "work"
            work.mkdir()
            buffer = io.StringIO()
            with mock.patch.object(host.subprocess, "run", fake_run), redirect_stdout(buffer):
                status = host.main(["run", "--plan-file", plan_file, "--work-dir", str(work)])
            config = (work / "moodle" / "config.php").read_text() if (work / "moodle" / "config.php").exists() else ""
            return status, calls, buffer.getvalue(), config

    def test_run_clones_writes_config_installs_and_probes(self):
        status, calls, log, config = self.run_host([])
        self.assertEqual(status, 0)
        commands = [call[0] for call in calls]
        self.assertEqual(commands[0][:2], ["git", "clone"])
        self.assertIn("admin/cli/install_database.php", commands[1][3])
        self.assertEqual(commands[2][-1], str(FIXTURE / "probe.php"))
        self.assertEqual(calls[2][1], FIXTURE)
        self.assertEqual(calls[2][2]["MOODLE_HOST_CHECK_CODE_DIR"], str(FIXTURE))
        self.assertTrue(calls[2][2]["MOODLE_HOST_CHECK_CONFIG"].endswith("moodle/config.php"))
        self.assertIn("$CFG->dbtype = 'pgsql';", config)

    def test_admin_password_is_random_and_never_printed(self):
        _, calls, log, _ = self.run_host([])
        password = next(part for part in calls[1][0] if part.startswith("--adminpass="))
        secret = password.split("=", 1)[1]
        self.assertNotEqual(secret, "<generated>")
        self.assertNotIn(secret, log)
        self.assertIn("--adminpass=<generated>", log)

    def test_first_failure_stops_the_run(self):
        status, calls, log, _ = self.run_host([0, 3])
        self.assertEqual(status, 1)
        self.assertEqual(len(calls), 2)
        self.assertIn("::error::command failed with exit status 3", log)


if __name__ == "__main__":
    unittest.main()
