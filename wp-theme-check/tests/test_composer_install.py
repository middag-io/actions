"""Tests of scripts/composer_install.py with a fake composer on PATH.

The fake composer records its arguments, its working directory and the
COMPOSER_AUTH it received, so the tests check that the credentials reach
Composer only through the environment and that no auth.json is written.

Run: python3 -I -m unittest discover -s tests -v (from the component folder).
"""

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import composer_install  # noqa: E402

FAKE_COMPOSER = """#!/bin/sh
{
  printf 'args=%s\\n' "$*"
  printf 'cwd=%s\\n' "$(pwd)"
  printf 'auth=%s\\n' "${COMPOSER_AUTH:-}"
  printf 'leak=%s\\n' "${COMPOSER_AUTH_PASSWORD:-}"
} > "$FAKE_COMPOSER_LOG"
exit "${FAKE_COMPOSER_EXIT:-0}"
"""


class ComposerEnvTest(unittest.TestCase):
    def test_no_host_leaves_no_auth(self):
        environ = {"PATH": "/bin", "COMPOSER_AUTH_HOST": "", "COMPOSER_AUTH_PASSWORD": ""}
        child = composer_install.composer_env(environ)
        self.assertEqual(child, {"PATH": "/bin"})

    def test_host_builds_http_basic(self):
        environ = {
            "PATH": "/bin",
            "COMPOSER_AUTH_HOST": "packages.example.com",
            "COMPOSER_AUTH_USERNAME": "ci",
            "COMPOSER_AUTH_PASSWORD": "s3cret",
        }
        child = composer_install.composer_env(environ)
        self.assertEqual(
            json.loads(child["COMPOSER_AUTH"]),
            {"http-basic": {"packages.example.com": {"username": "ci", "password": "s3cret"}}},
        )
        self.assertNotIn("COMPOSER_AUTH_PASSWORD", child)
        self.assertEqual(environ["COMPOSER_AUTH_PASSWORD"], "s3cret")


class MainTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bin = os.path.join(self.temp.name, "bin")
        self.theme = os.path.join(self.temp.name, "theme")
        os.mkdir(self.bin)
        os.mkdir(self.theme)
        fake = os.path.join(self.bin, "composer")
        with open(fake, "w", encoding="utf-8") as handle:
            handle.write(FAKE_COMPOSER)
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)
        self.log = os.path.join(self.temp.name, "composer.log")

    def run_main(self, **extra):
        environ = {
            "PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "FAKE_COMPOSER_LOG": self.log,
            "WORKING_DIRECTORY": self.theme,
            **extra,
        }
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = composer_install.main(environ)
        return code, stdout.getvalue()

    def logged(self):
        with open(self.log, encoding="utf-8") as handle:
            return dict(line.split("=", 1) for line in handle.read().splitlines())

    def test_installs_with_auth_in_the_environment_only(self):
        code, printed = self.run_main(
            COMPOSER_AUTH_HOST="packages.example.com",
            COMPOSER_AUTH_USERNAME="ci",
            COMPOSER_AUTH_PASSWORD="s3cret",
        )
        self.assertEqual(code, 0)
        logged = self.logged()
        self.assertEqual(logged["args"], "install --no-interaction --prefer-dist --no-progress")
        self.assertEqual(os.path.realpath(logged["cwd"]), os.path.realpath(self.theme))
        self.assertEqual(json.loads(logged["auth"])["http-basic"]["packages.example.com"]["password"], "s3cret")
        self.assertEqual(logged["leak"], "")
        self.assertEqual(os.listdir(self.theme), [])
        self.assertNotIn("s3cret", printed)

    def test_installs_without_auth(self):
        code, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(self.logged()["auth"], "")

    def test_reports_a_failed_install(self):
        code, printed = self.run_main(FAKE_COMPOSER_EXIT="3")
        self.assertEqual(code, 3)
        self.assertIn("::error::composer install failed with exit code 3", printed)

    def test_rejects_an_empty_working_directory(self):
        code, printed = self.run_main(WORKING_DIRECTORY="")
        self.assertEqual(code, 1)
        self.assertIn("WORKING_DIRECTORY is empty", printed)

    def test_reports_a_missing_composer(self):
        environ = {"PATH": self.temp.name, "WORKING_DIRECTORY": self.theme}
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = composer_install.main(environ)
        self.assertEqual(code, 1)
        self.assertIn("composer not found", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
