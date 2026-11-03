"""Tests of scripts/package.py, standard library only, without network.

The build tests need rsync, as the runner does; they are skipped where it is
missing.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import package  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "basic")
EXPECTED = os.path.join(HERE, "fixtures", "expected", "basic.sha256")
SHA = "0123456789abcdef0123456789abcdef01234567"
VERSION_FILE = """<?php
// Moodle reads $plugin->version alone.
$plugin->component = 'local_example';
$plugin->version = 2026110300;
$plugin->release = '1.2.0';
"""


def run_main(argv, cwd=None):
    """Runs package.main in cwd and returns (exit status, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    previous = os.getcwd()
    try:
        if cwd:
            os.chdir(cwd)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = package.main(argv)
    finally:
        os.chdir(previous)
    return status, out.getvalue(), err.getvalue()


def run_check(path=FIXTURE, **overrides):
    """Runs the check subcommand on path, given relative to the workspace (its parent)."""
    return run_main(check_args(path=os.path.basename(path), **overrides), cwd=os.path.dirname(path))


def check_args(**overrides):
    values = {"path": FIXTURE, "zip-name": "local_example", "tag-name": "v1.2.0", "sha": SHA,
              "dist-excludes-file": ".distignore"}
    values.update(overrides)
    argv = ["check"]
    for key, value in values.items():
        argv.append(f"--{key}={value}")
    return argv


class VersionFileTest(unittest.TestCase):
    def test_reads_version_release_and_component(self):
        self.assertEqual(
            package.parse_version_file(VERSION_FILE),
            {"version": "2026110300", "release": "1.2.0", "component": "local_example"},
        )

    def test_accepts_a_decimal_version_and_double_quotes(self):
        text = '$plugin->version = 2026110300.01;\n$plugin->release = "2.0";\n'
        self.assertEqual(package.parse_version_file(text)["version"], "2026110300.01")

    def test_ignores_the_name_in_a_comment(self):
        with self.assertRaisesRegex(package.InputError, r"plugin->version"):
            package.parse_version_file("// $plugin->version = 2026110300;\n$plugin->release = '1';\n")

    def test_requires_a_ten_digit_version(self):
        with self.assertRaisesRegex(package.InputError, "YYYYMMDDXX"):
            package.parse_version_file("$plugin->version = 2026;\n$plugin->release = '1';\n")

    def test_requires_a_release(self):
        with self.assertRaisesRegex(package.InputError, "release"):
            package.parse_version_file("$plugin->version = 2026110300;\n$plugin->release = ' ';\n")


class NamesTest(unittest.TestCase):
    def test_archive_name_uses_the_tag(self):
        self.assertEqual(package.archive_name("local_example", "v1.2.0", SHA), "local_example-v1.2.0.zip")

    def test_archive_name_falls_back_to_the_short_sha(self):
        self.assertEqual(package.archive_name("local_example", "", SHA), "local_example-01234567.zip")

    def test_rejects_names_that_are_not_frankenstyle(self):
        for name in ("moodle-local_example", "Local_example", "example", "local_ex/ample", ""):
            with self.subTest(name=name), self.assertRaises(package.InputError):
                package.validate_names(name, "v1", SHA)

    def test_rejects_unsafe_tags(self):
        for tag in ("../v1", "v1 v2", "v1;rm", "-v1", "v1/x"):
            with self.subTest(tag=tag), self.assertRaises(package.InputError):
                package.validate_names("local_example", tag, SHA)

    def test_requires_the_sha_without_a_tag(self):
        with self.assertRaisesRegex(package.InputError, "SHA"):
            package.validate_names("local_example", "", "")


class RelativePathTest(unittest.TestCase):
    def test_refuses_absolute_and_parent_paths(self):
        for value in ("/etc/passwd", "../outside", "a/../../b", ""):
            with self.subTest(value=value), self.assertRaises(package.InputError):
                package.relative_path(FIXTURE, value, "dist-excludes-file")

    def test_refuses_a_link_out_of_the_tree(self):
        with tempfile.TemporaryDirectory() as tree:
            os.symlink("/", os.path.join(tree, "root"))
            with self.assertRaisesRegex(package.InputError, "outside"):
                package.relative_path(tree, "root", "npm-working-dir")


class ExcludesFileTest(unittest.TestCase):
    def test_missing_default_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tree:
            self.assertEqual(package.resolve_excludes_file(tree, ".distignore"), "")

    def test_missing_explicit_file_fails(self):
        with tempfile.TemporaryDirectory() as tree:
            with self.assertRaisesRegex(package.InputError, "does not exist"):
                package.resolve_excludes_file(tree, "config/dist-excludes.release")

    def test_empty_value_means_no_file(self):
        self.assertEqual(package.resolve_excludes_file(FIXTURE, ""), "")


class CheckCommandTest(unittest.TestCase):
    def test_plans_the_fixture(self):
        status, out, err = run_check()
        self.assertEqual(status, 0, err)
        self.assertIn("version=2026110300\n", out)
        self.assertIn("release=1.2.0\n", out)
        self.assertIn("archive-name=local_example-v1.2.0.zip\n", out)
        self.assertIn("has-composer=false\n", out)
        self.assertIn("has-npm=false\n", out)

    def test_writes_github_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "output")
            os.environ["GITHUB_OUTPUT"] = output
            try:
                status, _out, err = run_check()
            finally:
                del os.environ["GITHUB_OUTPUT"]
            self.assertEqual(status, 0, err)
            with open(output, encoding="utf-8") as handle:
                self.assertIn("archive-name=local_example-v1.2.0.zip\n", handle.read())

    def test_fails_when_component_differs_from_zip_name(self):
        status, _out, err = run_check(**{"zip-name": "local_other"})
        self.assertEqual(status, 1)
        self.assertIn("$plugin->component", err)

    def test_fails_without_version_file(self):
        with tempfile.TemporaryDirectory() as tree:
            status, _out, err = run_check(tree)
        self.assertEqual(status, 1)
        self.assertIn("version.php not found", err)

    def test_release_upload_needs_a_tag(self):
        status, _out, err = run_check(**{"tag-name": "", "upload-release-asset": "true"})
        self.assertEqual(status, 1)
        self.assertIn("needs tag-name", err)

    def test_rejects_a_non_boolean_flag(self):
        status, _out, err = run_check(**{"dry-run": "yes"})
        self.assertEqual(status, 1)
        self.assertIn("true or false", err)

    def test_npm_build_needs_a_package_json(self):
        status, _out, err = run_check(**{"npm-build-command": "build", "npm-working-dir": "lang"})
        self.assertEqual(status, 1)
        self.assertIn("package.json", err)

    def test_npm_build_command_is_a_script_name(self):
        status, _out, err = run_check(**{"npm-build-command": "build && curl x"})
        self.assertEqual(status, 1)
        self.assertIn("npm script name", err)

    def test_reports_composer_and_npm(self):
        with tempfile.TemporaryDirectory() as tree:
            shutil.copytree(FIXTURE, tree, dirs_exist_ok=True)
            os.makedirs(os.path.join(tree, "ui"))
            for name in ("composer.json", os.path.join("ui", "package.json")):
                with open(os.path.join(tree, name), "w", encoding="utf-8") as handle:
                    handle.write("{}\n")
            status, out, err = run_check(tree, **{"npm-build-command": "build:dist", "npm-working-dir": "ui"})
        self.assertEqual(status, 0, err)
        self.assertIn("has-composer=true\n", out)
        self.assertIn("has-npm=true\n", out)

    def test_refuses_a_line_break_in_an_output(self):
        with self.assertRaisesRegex(package.InputError, "line break"):
            package.write_outputs({"release": "1.0\nx=y"})


@unittest.skipUnless(shutil.which("rsync"), "rsync is not installed")
class BuildCommandTest(unittest.TestCase):
    def build(self, out_dir, excludes_file=".distignore"):
        return run_main([
            "build", f"--path={FIXTURE}", "--zip-name=local_example", "--archive-name=local_example-v1.2.0.zip",
            f"--excludes-file={os.path.join(FIXTURE, excludes_file) if excludes_file else ''}",
            f"--out-dir={out_dir}",
        ])

    def test_listing_matches_the_expected_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, out, err = self.build(os.path.join(tmp, "out"))
            self.assertEqual(status, 0, err)
            with open(EXPECTED, encoding="utf-8") as handle:
                expected = handle.read()
            with open(os.path.join(tmp, "out", "listing.sha256"), encoding="utf-8") as handle:
                self.assertEqual(handle.read(), expected)
            self.assertIn(f"listing-sha256={hashlib.sha256(expected.encode()).hexdigest()}\n", out)
            self.assertIn(f"files={len(expected.splitlines())}\n", out)

    def test_excludes_default_paths_and_the_exclude_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, _out, err = self.build(os.path.join(tmp, "out"))
            self.assertEqual(status, 0, err)
            with zipfile.ZipFile(os.path.join(tmp, "out", "local_example-v1.2.0.zip")) as archive:
                names = archive.namelist()
        self.assertIn("local_example/version.php", names)
        for excluded in ("tests/", ".github/", "drafts/"):
            self.assertFalse(any(name.startswith(f"local_example/{excluded}") for name in names), excluded)

    def test_without_exclude_file_ships_drafts(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, out, err = self.build(os.path.join(tmp, "out"), excludes_file="")
        self.assertEqual(status, 0, err)
        self.assertIn("local_example/drafts/notes.txt", out)

    def test_zip_is_deterministic_and_keeps_the_executable_bit(self):
        digests = []
        with tempfile.TemporaryDirectory() as tmp:
            for run in ("a", "b"):
                status, _out, err = self.build(os.path.join(tmp, run))
                self.assertEqual(status, 0, err)
                archive_path = os.path.join(tmp, run, "local_example-v1.2.0.zip")
                with open(archive_path, "rb") as handle:
                    digests.append(hashlib.sha256(handle.read()).hexdigest())
            with zipfile.ZipFile(archive_path) as archive:
                mode = archive.getinfo("local_example/bin/run.sh").external_attr >> 16
        self.assertEqual(digests[0], digests[1])
        self.assertEqual(mode, package.EXECUTABLE_MODE)

    def test_refuses_a_non_empty_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "stale.zip"), "w", encoding="utf-8") as handle:
                handle.write("x")
            status, _out, err = self.build(tmp)
        self.assertEqual(status, 1)
        self.assertIn("not empty", err)


class LockfileTest(unittest.TestCase):
    def test_reads_lockfile_v3_packages(self):
        lock = {"lockfileVersion": 3, "packages": {
            "": {"name": "ui"},
            "node_modules/@scope/private": {"resolved": "https://npm.pkg.github.com/download/@scope/private/1.0.0/x"},
            "node_modules/a/node_modules/@scope/nested": {"resolved": "https://npm.pkg.github.com/download/@scope/nested/2.0.0/y"},
            "node_modules/react": {"resolved": "https://registry.npmjs.org/react/-/react-19.0.0.tgz"},
        }}
        self.assertEqual(package.github_registry_packages(json.dumps(lock)), ["@scope/nested", "@scope/private"])

    def test_reads_lockfile_v1_dependencies(self):
        lock = {"lockfileVersion": 1, "dependencies": {
            "a": {"resolved": "https://registry.npmjs.org/a/-/a-1.0.0.tgz", "dependencies": {
                "@scope/deep": {"resolved": "https://npm.pkg.github.com/download/@scope/deep/1.0.0/z"},
            }},
        }}
        self.assertEqual(package.github_registry_packages(json.dumps(lock)), ["@scope/deep"])

    def test_no_lockfile_means_nothing_to_check(self):
        with tempfile.TemporaryDirectory() as tree:
            status, out, _err = run_main(["npm-access", f"--working-dir={tree}"])
        self.assertEqual(status, 0)
        self.assertIn("nothing to check", out)


if __name__ == "__main__":
    unittest.main()
