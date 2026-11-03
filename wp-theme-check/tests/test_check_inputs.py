"""Tests of scripts/check_inputs.py on the fixture theme.

Run: python3 -I -m unittest discover -s tests -v (from the component folder).
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
REPOSITORY = os.path.dirname(COMPONENT)
FIXTURE = os.path.relpath(os.path.join(HERE, "fixtures", "basic"), REPOSITORY)
sys.path.insert(0, os.path.join(COMPONENT, "scripts"))

import check_inputs  # noqa: E402


def args(**overrides):
    values = {
        "php-version": "8.4",
        "php-extensions": "mbstring, xml",
        "has-cs-fixer": "true",
        "has-phpstan": "true",
        "has-rector": "false",
        "working-directory": FIXTURE,
        "composer-auth-host": "",
        "composer-auth-username": "",
        "has-composer-auth-password": "false",
        "dry-run": "false",
    }
    values.update({key.replace("_", "-"): value for key, value in overrides.items()})
    return check_inputs.parse_args([f"--{key}={value}" for key, value in values.items()])


class PlanTest(unittest.TestCase):
    def plan(self, **overrides):
        return check_inputs.plan(args(**overrides), REPOSITORY)

    def assert_rejected(self, fragment, **overrides):
        with self.assertRaises(check_inputs.InputError) as caught:
            self.plan(**overrides)
        self.assertIn(fragment, str(caught.exception))

    def test_default_checks_in_run_order(self):
        self.assertEqual(self.plan(), ["php-cs-fixer", "phpstan"])

    def test_every_check(self):
        self.assertEqual(self.plan(has_rector="true"), ["php-cs-fixer", "phpstan", "rector"])

    def test_rejects_no_check(self):
        self.assert_rejected("every check is disabled", has_cs_fixer="false", has_phpstan="false")

    def test_rejects_bad_php_version(self):
        self.assert_rejected("php-version", php_version="8")
        self.assert_rejected("php-version", php_version="8.4; rm")

    def test_rejects_bad_extensions(self):
        self.assert_rejected("php-extensions", php_extensions="mbstring; curl")

    def test_rejects_non_boolean(self):
        self.assert_rejected("has-rector", has_rector="yes")
        self.assert_rejected("dry-run", dry_run="1")

    def test_rejects_parent_directory(self):
        self.assert_rejected("inside the checkout", working_directory="../outside")

    def test_rejects_absolute_directory(self):
        self.assert_rejected("relative path", working_directory="/etc")

    def test_rejects_missing_directory(self):
        self.assert_rejected("not found", working_directory="no-such-theme")

    def test_rejects_directory_without_composer_json(self):
        self.assert_rejected("no composer.json", working_directory=os.path.relpath(HERE, REPOSITORY))

    def test_rejects_invalid_composer_json(self):
        with tempfile.TemporaryDirectory(dir=HERE) as directory:
            with open(os.path.join(directory, "composer.json"), "w", encoding="utf-8") as handle:
                handle.write("[1, 2")
            self.assert_rejected("not valid JSON", working_directory=os.path.relpath(directory, REPOSITORY))

    def test_auth_with_password(self):
        plan = self.plan(
            composer_auth_host="packages.example.com",
            composer_auth_username="ci",
            has_composer_auth_password="true",
        )
        self.assertEqual(plan, ["php-cs-fixer", "phpstan"])

    def test_auth_dry_run_without_password(self):
        plan = self.plan(composer_auth_host="packages.example.com:8443", composer_auth_username="ci", dry_run="true")
        self.assertEqual(plan, ["php-cs-fixer", "phpstan"])

    def test_rejects_auth_without_password(self):
        self.assert_rejected(
            "WP_THEME_CHECK_COMPOSER_AUTH_PASSWORD", composer_auth_host="packages.example.com", composer_auth_username="ci"
        )

    def test_rejects_auth_without_username(self):
        self.assert_rejected(
            "WP_THEME_CHECK_COMPOSER_AUTH_USERNAME",
            composer_auth_host="packages.example.com",
            has_composer_auth_password="true",
        )

    def test_rejects_username_without_host(self):
        self.assert_rejected("WP_THEME_CHECK_COMPOSER_AUTH_HOST", composer_auth_username="ci")

    def test_rejects_bad_host(self):
        self.assert_rejected(
            "composer-auth-host",
            composer_auth_host="https://packages.example.com",
            composer_auth_username="ci",
            has_composer_auth_password="true",
        )


class MainTest(unittest.TestCase):
    def run_main(self, argv, output_path):
        environ = {"GITHUB_OUTPUT": output_path, "GITHUB_WORKSPACE": REPOSITORY}
        stdout = io.StringIO()
        with unittest.mock.patch.dict(os.environ, environ), contextlib.redirect_stdout(stdout):
            code = check_inputs.main(argv)
        return code, stdout.getvalue()

    def argv(self, **overrides):
        values = vars(args(**overrides))
        return [f"--{key.replace('_', '-')}={value}" for key, value in values.items()]

    def test_dry_run_writes_checks_and_plan(self):
        with tempfile.NamedTemporaryFile("r", encoding="utf-8") as output:
            code, printed = self.run_main(self.argv(dry_run="true"), output.name)
            self.assertEqual(code, 0)
            self.assertEqual(output.read(), "checks=php-cs-fixer phpstan\n")
        self.assertIn("dry run, would run", printed)
        self.assertIn("vendor/bin/phpstan analyse", printed)
        self.assertNotIn("rector", printed)

    def test_error_is_an_annotation(self):
        with tempfile.NamedTemporaryFile("r", encoding="utf-8") as output:
            code, printed = self.run_main(self.argv(php_version="latest"), output.name)
            self.assertEqual(code, 1)
            self.assertEqual(output.read(), "")
        self.assertTrue(printed.startswith("::error::input php-version"))


if __name__ == "__main__":
    unittest.main()
