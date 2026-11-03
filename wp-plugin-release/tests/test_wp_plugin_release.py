"""Tests of scripts/wp_plugin_release.py: input checks and the archive.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import contextlib
import hashlib
import io
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
FIXTURE = os.path.join(HERE, "fixtures", "basic")
EXPECTED = os.path.join(HERE, "fixtures", "expected", "basic.txt")
sys.path.insert(0, os.path.join(COMPONENT, "scripts"))

import wp_plugin_release as release  # noqa: E402


def run(argv, env=None):
    """Runs main() in the current directory; returns (status, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    saved = dict(os.environ)
    os.environ.pop("GITHUB_OUTPUT", None)
    os.environ.pop("COMPOSER_AUTH", None)
    os.environ.update(env or {})
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = release.main(argv)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    return status, out.getvalue(), err.getvalue()


def outputs_of(text):
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


class WorkspaceTest(unittest.TestCase):
    """Each test runs in a temporary workspace holding a copy of the fixture."""

    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.workspace)
        self.plugin = os.path.join(self.workspace, "plugin")
        shutil.copytree(FIXTURE, self.plugin)
        # composer install writes composer.lock; the fixture listing has it.
        with open(os.path.join(self.plugin, "composer.lock"), "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        self.previous = os.getcwd()
        os.chdir(self.workspace)
        self.addCleanup(os.chdir, self.previous)

    def write(self, relative, text=""):
        full = os.path.join(self.plugin, relative)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(text)
        return full


class ValidateTest(WorkspaceTest):
    def validate(self, env=None, **overrides):
        values = {
            "path": "plugin",
            "tag-name": "1.2.3",
            "zip-name": "example-plugin",
            "has-ui": "false",
            "has-strauss": "false",
            "ui-check-file": "assets/dist/app.js",
            "npm-github-packages-scope": "",
            "extra-zip-excludes": "",
            "extra-zip-excludes-file": "",
            "dry-run": "true",
            "has-app-credentials": "false",
        }
        values.update({key.replace("_", "-"): value for key, value in overrides.items()})
        argv = ["validate"] + [f"--{key}={value}" for key, value in values.items()]
        return run(argv, env)

    def test_dry_run_needs_no_credentials(self):
        status, out, err = self.validate()
        self.assertEqual(status, 0, err)
        self.assertEqual(outputs_of(out)["archive-name"], "example-plugin-1.2.3.zip")

    def test_release_requires_the_app_credentials(self):
        status, _, err = self.validate(dry_run="false")
        self.assertEqual(status, 1)
        self.assertIn("app-client-id and app-private-key", err)
        self.assertIn("WP_PLUGIN_RELEASE_APP_PRIVATE_KEY", err)
        status, _, err = self.validate(dry_run="false", has_app_credentials="true")
        self.assertEqual(status, 0, err)

    def test_rejects_bad_tag_and_zip_names(self):
        for tag in ("", "../1.0", "v1/2", "1.0 2", "-1.0", "1..0"):
            with self.subTest(tag=tag):
                self.assertEqual(self.validate(tag_name=tag)[0], 1)
        for name in ("", "a/b", "a b", ".hidden", "a..b"):
            with self.subTest(name=name):
                self.assertEqual(self.validate(zip_name=name)[0], 1)
        for tag in ("1.2.3", "2.0.0-rc.1", "plugin-v1.0.0", "1.0.0+build.5"):
            with self.subTest(tag=tag):
                self.assertEqual(self.validate(tag_name=tag)[0], 0)

    def test_rejects_paths_out_of_the_workspace(self):
        for path in ("/etc", "../plugin", "plugin/../..", "missing"):
            with self.subTest(path=path):
                self.assertEqual(self.validate(path=path)[0], 1)

    def test_rejects_non_boolean_flags(self):
        self.assertEqual(self.validate(has_ui="yes")[0], 1)
        self.assertEqual(self.validate(dry_run="1")[0], 1)

    def test_rejects_unsafe_exclude_patterns(self):
        for pattern in ("a;b", "$(x)", "a|b", "`x`", "a>b"):
            with self.subTest(pattern=pattern):
                status, _, err = self.validate(extra_zip_excludes=pattern)
                self.assertEqual(status, 1)
                self.assertIn("unsafe ZIP exclude pattern", err)
        self.assertEqual(self.validate(extra_zip_excludes="phpunit.xml *.dist.php")[0], 0)

    def test_excludes_file_must_exist(self):
        status, _, err = self.validate(extra_zip_excludes_file="missing.txt")
        self.assertEqual(status, 1)
        self.assertIn("extra-zip-excludes-file", err)
        self.write("zip-excludes.txt", "# comment\n\nsrc/*\n")
        self.assertEqual(self.validate(extra_zip_excludes_file="zip-excludes.txt")[0], 0)

    def test_ui_needs_a_package_json(self):
        status, _, err = self.validate(has_ui="true")
        self.assertEqual(status, 1)
        self.assertIn("ui/package.json", err)
        self.write("ui/package.json", "{}\n")
        self.assertEqual(self.validate(has_ui="true")[0], 0)

    def test_npm_scope_rules(self):
        self.write("ui/package.json", "{}\n")
        self.assertEqual(self.validate(has_ui="true", npm_github_packages_scope="@example")[0], 0)
        self.assertEqual(self.validate(has_ui="true", npm_github_packages_scope="example")[0], 1)
        self.assertEqual(self.validate(has_ui="false", npm_github_packages_scope="@example")[0], 1)

    def test_composer_auth_must_be_a_json_object_and_is_never_printed(self):
        secret = "not-json-s3cr3t"
        status, out, err = self.validate(env={"COMPOSER_AUTH": secret})
        self.assertEqual(status, 1)
        self.assertNotIn(secret, out + err)
        status, out, err = self.validate(env={"COMPOSER_AUTH": '["s3cr3t"]'})
        self.assertEqual(status, 1)
        self.assertNotIn("s3cr3t", out + err)
        auth = '{"http-basic": {"repo.example.com": {"username": "u", "password": "s3cr3t"}}}'
        status, out, err = self.validate(env={"COMPOSER_AUTH": auth})
        self.assertEqual(status, 0, err)
        # The script prints no credential, not even as a mask command: the
        # first step of action.yml masks them in bash (tests/test_action.py).
        self.assertNotIn("s3cr3t", out + err)
        self.assertNotIn("::add-mask::", out)


class ArchiveTest(WorkspaceTest):
    def archive(self, extra="phpunit.xml", extra_file=""):
        out_dir = os.path.join(self.workspace, "out")
        status, out, err = run(
            [
                "archive",
                "--path=plugin",
                "--tag-name=1.2.3",
                "--zip-name=example-plugin",
                f"--extra-zip-excludes={extra}",
                f"--extra-zip-excludes-file={extra_file}",
                f"--out-dir={out_dir}",
            ]
        )
        return status, outputs_of(out), err

    def names(self, outputs):
        with zipfile.ZipFile(outputs["archive-path"]) as archive:
            return archive.namelist()

    def test_fixture_gives_the_expected_listing(self):
        status, outputs, err = self.archive()
        self.assertEqual(status, 0, err)
        with open(EXPECTED, encoding="utf-8") as handle:
            expected = handle.read()
        self.assertEqual(self.names(outputs), expected.splitlines())
        self.assertEqual(outputs["archive-name"], "example-plugin-1.2.3.zip")
        self.assertEqual(outputs["files"], str(len(expected.splitlines())))
        self.assertEqual(outputs["files-sha256"], hashlib.sha256(expected.encode()).hexdigest())

    def test_archive_is_reproducible(self):
        first = self.archive()[1]["archive-sha256"]
        shutil.rmtree(os.path.join(self.workspace, "out"))
        os.utime(os.path.join(self.plugin, "readme.txt"), (0, 0))
        second = self.archive()[1]["archive-sha256"]
        self.assertEqual(first, second)

    def test_default_excludes_apply_at_the_root_only(self):
        self.write("node_modules/pkg/index.js")
        self.write("ui/node_modules/pkg/index.js")
        self.write("ui/src/app.ts")
        self.write("ui/package.json", "{}\n")
        self.write("assets/dist/app.js")
        self.write("vendor/tests/keep.php")
        self.write("auth.json", "{}")
        names = self.names(self.archive()[1])
        self.assertIn("ui/package.json", names)
        self.assertIn("assets/dist/app.js", names)
        self.assertIn("vendor/tests/keep.php", names)
        for name in names:
            with self.subTest(name=name):
                self.assertFalse(name.startswith(("node_modules/", "ui/node_modules/", "ui/src/", "tests/", "docs/")))
                self.assertNotEqual(name, "auth.json")

    def test_extra_excludes_from_input_and_file(self):
        self.write("zip-excludes.txt", "# keep the PHP sources out\nsrc/*\nzip-excludes.txt\n")
        names = self.names(self.archive(extra="phpunit.xml readme.txt", extra_file="zip-excludes.txt")[1])
        self.assertEqual(names, ["composer.json", "composer.lock", "example-plugin.php"])

    def test_empty_archive_fails(self):
        status, _, err = self.archive(extra="*")
        self.assertEqual(status, 1)
        self.assertIn("empty", err)

    @unittest.skipIf(os.name != "posix", "symbolic links")
    def test_symbolic_links(self):
        os.symlink("example-plugin.php", os.path.join(self.plugin, "alias.php"))
        status, outputs, err = self.archive()
        self.assertEqual(status, 0, err)
        self.assertIn("alias.php", self.names(outputs))
        outside = os.path.join(self.workspace, "outside.txt")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write("x")
        os.symlink(outside, os.path.join(self.plugin, "leak.txt"))
        status, _, err = self.archive()
        self.assertEqual(status, 1)
        self.assertIn("leak.txt", err)
        os.remove(os.path.join(self.plugin, "leak.txt"))
        os.symlink(os.path.join(self.plugin, "src"), os.path.join(self.plugin, "linked-dir"))
        status, _, err = self.archive()
        self.assertEqual(status, 1)
        self.assertIn("linked-dir", err)

    def test_check_ui(self):
        status, _, err = run(["check-ui", "--path=plugin", "--ui-check-file=assets/dist/app.js"])
        self.assertEqual(status, 1)
        self.assertIn("assets/dist/app.js", err)
        self.write("assets/dist/app.js")
        self.assertEqual(run(["check-ui", "--path=plugin", "--ui-check-file=assets/dist/app.js"])[0], 0)


if __name__ == "__main__":
    unittest.main()
