"""Tests of scripts/check.py, on the fixture plugin and on temporary trees.

Nothing here runs composer or npm: run_commands takes a fake runner.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(COMPONENT, "scripts"))

import check  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "basic")
EXPECTED = os.path.join(HERE, "fixtures", "expected")
WORKSPACE = os.path.dirname(COMPONENT)
FIXTURE_REL = os.path.relpath(FIXTURE, WORKSPACE)


def args_for(target, *extra):
    version = ["--php-version", "8.4"] if target == "php" and "--php-version" not in extra else []
    return check.parse_args(["plan", "--target", target, "--path", FIXTURE_REL, *version, *extra])


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class Result:
    def __init__(self, returncode):
        self.returncode = returncode


class FakeRunner:
    """Records each call; fails the commands whose line is in fail."""

    def __init__(self, fail=()):
        self.fail = set(fail)
        self.calls = []

    def __call__(self, argv, cwd, env, check):
        self.calls.append({"argv": argv, "cwd": cwd, "env": env})
        return Result(1 if " ".join(argv) in self.fail else 0)


class PlanTest(unittest.TestCase):
    def test_php_plan_matches_the_expected_listing(self):
        args = args_for("php", "--php-version", "8.4", "--composer-scripts", "check:style, check:stan, check:rector, test")
        text = check.plan_text(check.build_plan(args, WORKSPACE), WORKSPACE)
        self.assertEqual(text, read(os.path.join(EXPECTED, "php.plan")))

    def test_ui_plan_matches_the_expected_listing(self):
        args = args_for("ui", "--ui-directory", "ui", "--npm-scripts", "lint, build")
        text = check.plan_text(check.build_plan(args, WORKSPACE), WORKSPACE)
        self.assertEqual(text, read(os.path.join(EXPECTED, "ui.plan")))

    def test_ui_plan_with_scope_probes_each_package_of_the_scope_first(self):
        args = args_for("ui", "--npm-scripts", "lint", "--github-packages-npm-scope", "@example")
        argvs = [" ".join(c["argv"]) for c in check.build_plan(args, WORKSPACE)]
        self.assertEqual(argvs[:2], ["npm view @example/widgets version --registry=https://npm.pkg.github.com", "npm ci"])

    def test_ui_plan_with_another_scope_probes_nothing(self):
        args = args_for("ui", "--npm-scripts", "lint", "--github-packages-npm-scope", "@other")
        self.assertEqual(check.build_plan(args, WORKSPACE)[0]["argv"], ["npm", "ci"])

    def test_ui_plan_without_scope_has_no_registry_probe(self):
        args = args_for("ui", "--npm-scripts", "lint", "--typecheck", "false")
        argvs = [" ".join(c["argv"]) for c in check.build_plan(args, WORKSPACE)]
        self.assertEqual(argvs, ["npm ci", "npm run lint"])

    def test_php_plan_without_scripts_only_installs(self):
        argvs = [c["argv"][:2] for c in check.build_plan(args_for("php"), WORKSPACE)]
        self.assertEqual(argvs, [["composer", "install"]])

    def test_outputs_count_and_digest(self):
        commands = check.build_plan(args_for("php", "--composer-scripts", "test"), WORKSPACE)
        text = check.plan_text(commands, WORKSPACE)
        with tempfile.NamedTemporaryFile("r", encoding="utf-8", delete=False) as out:
            path = out.name
        try:
            os.environ["GITHUB_OUTPUT"] = path
            check.write_outputs(commands, text)
            lines = read(path).splitlines()
        finally:
            del os.environ["GITHUB_OUTPUT"]
            os.unlink(path)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.assertEqual(lines, ["commands=2", f"plan-sha256={digest}"])


class ValidationTest(unittest.TestCase):
    def assert_invalid(self, args, message):
        with self.assertRaises(check.InputError) as caught:
            check.build_plan(args, WORKSPACE)
        self.assertIn(message, str(caught.exception))

    def test_undeclared_composer_script_fails(self):
        self.assert_invalid(args_for("php", "--composer-scripts", "check:missing"), "not declared in composer.json")

    def test_invalid_composer_script_name_fails(self):
        self.assert_invalid(args_for("php", "--composer-scripts", "test; rm -rf /"), "invalid script name")

    def test_undeclared_npm_script_fails(self):
        self.assert_invalid(args_for("ui", "--npm-scripts", "deploy"), "not declared in package.json")

    def test_bad_php_version_fails(self):
        self.assert_invalid(args_for("php", "--php-version", "latest"), "php-version")

    def test_missing_php_version_fails(self):
        self.assert_invalid(args_for("php", "--php-version", ""), "php-version")

    def test_app_client_id_without_key_fails(self):
        self.assert_invalid(args_for("php", "--has-app-client-id", "true"), "go together")

    def test_bad_scope_fails(self):
        self.assert_invalid(args_for("ui", "--github-packages-npm-scope", "example"), "@owner")

    def test_parent_path_fails(self):
        args = check.parse_args(["plan", "--target", "php", "--path", "../outside", "--php-version", "8.4"])
        self.assert_invalid(args, "without '..'")

    def test_absolute_ui_directory_fails(self):
        self.assert_invalid(args_for("ui", "--ui-directory", "/tmp"), "relative path")

    def test_missing_ui_directory_fails(self):
        self.assert_invalid(args_for("ui", "--ui-directory", "frontend"), "not found")

    def test_bad_boolean_fails(self):
        with self.assertRaises(check.InputError):
            check.parse_args(["plan", "--target", "ui", "--typecheck", "yes"])

    def test_missing_lockfile_fails(self):
        tree = tempfile.mkdtemp()
        try:
            shutil.copytree(os.path.join(FIXTURE, "ui"), os.path.join(tree, "ui"))
            os.unlink(os.path.join(tree, "ui", "package-lock.json"))
            args = check.parse_args(["plan", "--target", "ui", "--path", "."])
            with self.assertRaises(check.InputError) as caught:
                check.build_plan(args, tree)
            self.assertIn("package-lock.json not found", str(caught.exception))
        finally:
            shutil.rmtree(tree)


class ComposerAuthTest(unittest.TestCase):
    def test_empty_gives_no_value(self):
        self.assertEqual(check.composer_auth("", ""), "")

    def test_app_token_is_added_for_github(self):
        auth = {"http-basic": {"repo.example.com": {"username": "u", "password": "p"}}}
        merged = json.loads(check.composer_auth(json.dumps(auth), "token"))
        self.assertEqual(merged["http-basic"], auth["http-basic"])
        self.assertEqual(merged["github-oauth"], {"github.com": "token"})

    def test_invalid_json_fails_without_echoing_it(self):
        with self.assertRaises(check.InputError) as caught:
            check.composer_auth("{secret-value", "")
        self.assertNotIn("secret-value", str(caught.exception))

    def test_non_object_fails(self):
        with self.assertRaises(check.InputError):
            check.composer_auth("[]", "")


class RunTest(unittest.TestCase):
    BASE_ENV = {
        "PATH": "/usr/bin",
        "WP_PLUGIN_CHECK_COMPOSER_AUTH": json.dumps({"bearer": {"repo.example.com": "b"}}),
        "WP_PLUGIN_CHECK_APP_TOKEN": "app-token",
        "WP_PLUGIN_CHECK_NPM_TOKEN": "npm-token",
    }

    def php_commands(self):
        return check.build_plan(args_for("php", "--composer-scripts", "check:style, test"), WORKSPACE)

    def ui_commands(self):
        args = args_for("ui", "--npm-scripts", "lint,build", "--github-packages-npm-scope", "@example")
        return check.build_plan(args, WORKSPACE)

    def test_composer_auth_reaches_only_the_install(self):
        runner = FakeRunner()
        self.assertEqual(check.run_commands(self.php_commands(), self.BASE_ENV, runner), [])
        install, *checks = runner.calls
        self.assertIn("COMPOSER_AUTH", install["env"])
        self.assertEqual(json.loads(install["env"]["COMPOSER_AUTH"])["github-oauth"], {"github.com": "app-token"})
        for call in runner.calls:
            self.assertFalse(any(k.startswith("WP_PLUGIN_CHECK_") for k in call["env"]))
        for call in checks:
            self.assertNotIn("COMPOSER_AUTH", call["env"])

    def test_npm_token_reaches_only_the_probe_and_the_install(self):
        runner = FakeRunner()
        self.assertEqual(check.run_commands(self.ui_commands(), self.BASE_ENV, runner), [])
        with_token = [" ".join(c["argv"][:2]) for c in runner.calls if "NODE_AUTH_TOKEN" in c["env"]]
        self.assertEqual(with_token, ["npm view", "npm ci"])

    def test_every_check_runs_after_a_failure(self):
        runner = FakeRunner(fail={"composer run-script --no-interaction check:style"})
        failed = check.run_commands(self.php_commands(), self.BASE_ENV, runner)
        self.assertEqual(failed, ["composer run-script --no-interaction check:style"])
        self.assertEqual(len(runner.calls), 3)

    def test_failed_install_stops(self):
        runner = FakeRunner(fail={"composer install --no-interaction --no-progress --prefer-dist"})
        failed = check.run_commands(self.php_commands(), self.BASE_ENV, runner)
        self.assertEqual(len(failed), 1)
        self.assertEqual(len(runner.calls), 1)

    def test_failed_probe_stops_before_the_install(self):
        probe = "npm view @example/widgets version --registry=https://npm.pkg.github.com"
        runner = FakeRunner(fail={probe})
        self.assertEqual(check.run_commands(self.ui_commands(), self.BASE_ENV, runner), [probe])
        self.assertEqual(len(runner.calls), 1)

    def test_invalid_composer_auth_fails_before_composer(self):
        env = {**self.BASE_ENV, "WP_PLUGIN_CHECK_COMPOSER_AUTH": "not json"}
        runner = FakeRunner()
        with self.assertRaises(check.InputError):
            check.run_commands(self.php_commands(), env, runner)
        self.assertEqual(runner.calls, [])


class MainTest(unittest.TestCase):
    def test_plan_mode_returns_zero_on_the_fixture(self):
        os.environ["GITHUB_WORKSPACE"] = WORKSPACE
        try:
            code = check.main(["plan", "--target", "php", "--path", FIXTURE_REL, "--php-version", "8.4", "--composer-scripts", "test"])
        finally:
            del os.environ["GITHUB_WORKSPACE"]
        self.assertEqual(code, 0)

    def test_invalid_input_returns_one(self):
        os.environ["GITHUB_WORKSPACE"] = WORKSPACE
        try:
            code = check.main(["plan", "--target", "php", "--path", FIXTURE_REL, "--php-version", "8.4", "--composer-scripts", "nope"])
        finally:
            del os.environ["GITHUB_WORKSPACE"]
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
