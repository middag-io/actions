"""Tests of scripts/composer_setup.py. Standard library only, no network."""

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import composer_setup  # noqa: E402

FIXTURE = HERE / "fixtures" / "basic"
EXPECTED = HERE / "fixtures" / "expected" / "basic.env"


def check_args(**overrides):
    values = {
        "php_version": "8.4",
        "php_extensions": "mbstring, xml",
        "coverage": "none",
        "working_directory": ".",
        "http_basic_host": "",
        "http_basic_username": "",
        "has_http_basic_password": "false",
        "has_github_token": "false",
        "install": "true",
        "no_dev": "false",
        "optimize_autoloader": "false",
        "dry_run": "false",
    }
    values.update(overrides)
    return composer_setup.parser().parse_args(
        ["check"] + [f"--{key.replace('_', '-')}={value}" for key, value in values.items()]
    )


def run_quietly(function, *args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        result = function(*args)
    return result, output.getvalue()


class BuildPlanTest(unittest.TestCase):
    def test_plans_install_without_credentials(self):
        plan = composer_setup.build_plan(check_args(), FIXTURE)
        self.assertEqual(plan["auth-entries"], "")
        self.assertEqual(plan["install-command"], "composer install --no-interaction --prefer-dist")

    def test_dry_run_matches_expected_ci_outputs(self):
        args = check_args(
            http_basic_host="repo.example.com",
            http_basic_username="example-user",
            no_dev="true",
            optimize_autoloader="true",
            dry_run="true",
        )
        plan = composer_setup.build_plan(args, FIXTURE)
        expected = dict(line.split("=", 1) for line in EXPECTED.read_text(encoding="utf-8").splitlines())
        self.assertEqual(plan["auth-entries"], expected["auth-entries"])
        self.assertEqual(plan["install-command"], expected["install-command"])

    def test_lists_github_token_entry(self):
        plan = composer_setup.build_plan(check_args(has_github_token="true"), FIXTURE)
        self.assertEqual(plan["auth-entries"], "github-oauth:github.com")

    def test_no_install_gives_empty_command(self):
        plan = composer_setup.build_plan(check_args(install="false"), FIXTURE)
        self.assertEqual(plan["install-command"], "")

    def test_rejects_invalid_inputs(self):
        cases = {
            "php-version": check_args(php_version="8"),
            "php-extensions": check_args(php_extensions="mbstring; rm -rf /"),
            "coverage": check_args(coverage="phpdbg"),
            "dry-run": check_args(dry_run="yes"),
            "no-dev": check_args(no_dev="1"),
        }
        for name, args in cases.items():
            with self.subTest(name=name), self.assertRaisesRegex(composer_setup.InputError, name):
                composer_setup.build_plan(args, FIXTURE)

    def test_rejects_working_directory_outside_checkout(self):
        for value in ("/etc", "../outside", "a/../../b"):
            with self.subTest(value=value), self.assertRaisesRegex(composer_setup.InputError, "relative path"):
                composer_setup.build_plan(check_args(working_directory=value), FIXTURE)

    def test_rejects_directory_without_composer_json(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(composer_setup.InputError, "no composer.json"):
                composer_setup.build_plan(check_args(), root)

    def test_rejects_missing_directory(self):
        with self.assertRaisesRegex(composer_setup.InputError, "not a directory"):
            composer_setup.build_plan(check_args(working_directory="missing"), FIXTURE)

    def test_http_basic_needs_host_and_username(self):
        for args in (check_args(http_basic_host="repo.example.com"), check_args(http_basic_username="user")):
            with self.subTest(args=args), self.assertRaisesRegex(composer_setup.InputError, "go together"):
                composer_setup.build_plan(args, FIXTURE)

    def test_http_basic_password_without_host_fails(self):
        with self.assertRaisesRegex(composer_setup.InputError, "http-basic-password is set"):
            composer_setup.build_plan(check_args(has_http_basic_password="true"), FIXTURE)

    def test_http_basic_needs_password_outside_dry_run(self):
        args = check_args(http_basic_host="repo.example.com", http_basic_username="user")
        with self.assertRaisesRegex(composer_setup.InputError, "PHP_COMPOSER_SETUP_HTTP_BASIC_PASSWORD"):
            composer_setup.build_plan(args, FIXTURE)

    def test_rejects_invalid_host_and_username(self):
        cases = (
            check_args(http_basic_host="https://repo.example.com", http_basic_username="u", has_http_basic_password="true"),
            check_args(http_basic_host="repo.example.com", http_basic_username="a b", has_http_basic_password="true"),
        )
        for args in cases:
            with self.subTest(args=args), self.assertRaises(composer_setup.InputError):
                composer_setup.build_plan(args, FIXTURE)

    def test_accepts_host_with_port(self):
        args = check_args(http_basic_host="repo.example.com:8443", http_basic_username="u", has_http_basic_password="true")
        plan = composer_setup.build_plan(args, FIXTURE)
        self.assertEqual(plan["auth-entries"], "http-basic:repo.example.com:8443")


class RunCheckTest(unittest.TestCase):
    def test_writes_outputs_and_reports_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            output_file = Path(temp) / "output"
            cwd = os.getcwd()
            os.chdir(FIXTURE)
            try:
                with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_file)}):
                    code, printed = run_quietly(composer_setup.main, ["check", "--php-version=8.4", "--dry-run=true"])
            finally:
                os.chdir(cwd)
            self.assertEqual(code, 0)
            self.assertIn("Dry run: would run in .: composer install", printed)
            self.assertIn("install-command=composer install --no-interaction --prefer-dist\n", output_file.read_text())

    def test_invalid_input_returns_error_annotation(self):
        code, printed = run_quietly(composer_setup.main, ["check", "--php-version=bad"])
        self.assertEqual(code, 1)
        self.assertTrue(printed.startswith("::error::php-version"))


class AuthTest(unittest.TestCase):
    def test_merge_keeps_existing_entries_and_input(self):
        existing = {"github-oauth": {"github.com": "old"}, "http-basic": {"other.example.com": {"username": "o", "password": "p"}}}
        merged = composer_setup.merge_auth(existing, "repo.example.com", "user", "secret", "")
        self.assertEqual(existing["http-basic"], {"other.example.com": {"username": "o", "password": "p"}})
        self.assertEqual(merged["github-oauth"], {"github.com": "old"})
        self.assertEqual(
            merged["http-basic"],
            {"other.example.com": {"username": "o", "password": "p"}, "repo.example.com": {"username": "user", "password": "secret"}},
        )

    def test_merge_replaces_github_token(self):
        merged = composer_setup.merge_auth({"github-oauth": {"github.com": "old"}}, "", "", "", "new")
        self.assertEqual(merged["github-oauth"], {"github.com": "new"})

    def test_auth_writes_file_with_private_mode_and_masks(self):
        env = {
            "HTTP_BASIC_HOST": "repo.example.com",
            "HTTP_BASIC_USERNAME": "user",
            "HTTP_BASIC_PASSWORD": "pass-value",
            "COMPOSER_GITHUB_TOKEN": "token-value",
        }
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, env):
            code, printed = run_quietly(composer_setup.main, ["auth", f"--composer-home={home}"])
            auth_file = Path(home) / "auth.json"
            data = json.loads(auth_file.read_text())
            mode = stat.S_IMODE(auth_file.stat().st_mode)
        self.assertEqual(code, 0)
        self.assertEqual(data["http-basic"]["repo.example.com"]["password"], "pass-value")
        self.assertEqual(data["github-oauth"]["github.com"], "token-value")
        self.assertEqual(mode, 0o600)
        self.assertNotIn("pass-value", printed)
        self.assertNotIn("token-value", printed)

    def test_auth_without_credentials_writes_nothing(self):
        env = {"HTTP_BASIC_HOST": "", "HTTP_BASIC_PASSWORD": "", "COMPOSER_GITHUB_TOKEN": ""}
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, env):
            code, _ = run_quietly(composer_setup.main, ["auth", f"--composer-home={home}"])
            self.assertEqual(code, 0)
            self.assertFalse((Path(home) / "auth.json").exists())

    def test_auth_keeps_private_mode_over_stale_temporary_file(self):
        env = {"HTTP_BASIC_HOST": "", "COMPOSER_GITHUB_TOKEN": "token-value"}
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, env):
            stale = Path(home) / "auth.json.tmp"
            stale.write_text("{}")
            stale.chmod(0o644)
            code, _ = run_quietly(composer_setup.main, ["auth", f"--composer-home={home}"])
            mode = stat.S_IMODE((Path(home) / "auth.json").stat().st_mode)
        self.assertEqual(code, 0)
        self.assertEqual(mode, 0o600)

    def test_auth_rejects_existing_entry_that_is_not_an_object(self):
        env = {"HTTP_BASIC_HOST": "repo.example.com", "HTTP_BASIC_USERNAME": "user", "HTTP_BASIC_PASSWORD": "p", "COMPOSER_GITHUB_TOKEN": ""}
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, env):
            (Path(home) / "auth.json").write_text('{"http-basic": []}')
            code, printed = run_quietly(composer_setup.main, ["auth", f"--composer-home={home}"])
        self.assertEqual(code, 1)
        self.assertIn("http-basic", printed)

    def test_auth_rejects_invalid_existing_file(self):
        env = {"HTTP_BASIC_HOST": "", "COMPOSER_GITHUB_TOKEN": "token-value"}
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, env):
            (Path(home) / "auth.json").write_text("[1, 2]")
            code, printed = run_quietly(composer_setup.main, ["auth", f"--composer-home={home}"])
        self.assertEqual(code, 1)
        self.assertIn("not a JSON object", printed)


class InstallTest(unittest.TestCase):
    def test_runs_composer_with_planned_arguments(self):
        completed = mock.Mock(returncode=0)
        with mock.patch.object(composer_setup.subprocess, "run", return_value=completed) as run:
            code, _ = run_quietly(
                composer_setup.main,
                ["install", "--working-directory=sub", "--no-dev=true", "--optimize-autoloader=true"],
            )
        self.assertEqual(code, 0)
        run.assert_called_once_with(
            ["composer", "install", "--no-interaction", "--prefer-dist", "--no-dev", "--optimize-autoloader"],
            cwd="sub",
            check=False,
        )

    def test_returns_composer_exit_code(self):
        with mock.patch.object(composer_setup.subprocess, "run", return_value=mock.Mock(returncode=2)):
            code, _ = run_quietly(composer_setup.main, ["install"])
        self.assertEqual(code, 2)



class ActionMaskTest(unittest.TestCase):
    """The auth step of action.yml masks the secrets before the script runs."""

    def auth_step_script(self):
        text = (Path(__file__).resolve().parents[1] / "action.yml").read_text(encoding="utf-8")
        step = text.split("- name: Write the Composer credentials", 1)[1].split("\n    - name:", 1)[0]
        body = step.split("run: |\n", 1)[1]
        return "\n".join(line[8:] for line in body.splitlines())

    def test_masks_every_line_before_the_script(self):
        script = self.auth_step_script()
        self.assertLess(script.index("::add-mask::"), script.index("composer_setup.py"))
        masking = script.split("python3", 1)[0]
        result = subprocess.run(
            ["bash", "-c", masking],
            env={"HTTP_BASIC_PASSWORD": "pass-one\npass-two", "COMPOSER_GITHUB_TOKEN": "", "PATH": os.environ["PATH"]},
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.splitlines(), ["::add-mask::pass-one", "::add-mask::pass-two"])

if __name__ == "__main__":
    unittest.main()
