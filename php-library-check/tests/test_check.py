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
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import check  # noqa: E402

FIXTURE = HERE / "fixtures" / "basic"
REPOSITORY = HERE.parent.parent
COMMIT = "0123456789abcdef0123456789abcdef01234567"
EXPECTED = json.loads((HERE / "fixtures" / "expected" / "basic.json").read_text())
EXPECTED_HIGHEST = json.loads((HERE / "fixtures" / "expected" / "highest.json").read_text())
# Inputs of the highest dry-run job of _ci.yml that reach check.py.
HIGHEST_INPUTS = {
    "php_version": "8.4", "composer_validate": "false", "strict_psr": "false", "lint_script": "",
    "phpstan_script": "", "cs_fixer_script": "", "rector_script": "", "composer_scripts": "",
    "expect_majors": "symfony/console:8\nsymfony/process:8",
}


def plan_args(**overrides):
    values = {
        "workspace": str(FIXTURE), "path": ".", "php_version": "8.2", "static_checks": "true",
        "composer_validate": "true", "composer_validate_strict": "false", "strict_psr": "true",
        "lint_script": "lint", "phpstan_script": "check:stan", "cs_fixer_script": "check:style",
        "rector_script": "check:rector", "composer_scripts": "check:schemas", "test_script": "test",
        "expect_majors": "", "dry_run": "true", "plan_file": "", "github_output": "",
        "dependency_versions": "locked", "tool_cache_paths": "", "runner_os": "Linux", "commit": COMMIT,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def read_outputs(text):
    """Parses a GITHUB_OUTPUT file: name=value lines and name<<delimiter blocks."""
    outputs, lines = {}, iter(text.splitlines())
    for line in lines:
        if "<<" in line and "=" not in line.split("<<", 1)[0]:
            name, delimiter = line.split("<<", 1)
            block = []
            for value in lines:
                if value == delimiter:
                    break
                block.append(value)
            outputs[name] = "\n".join(block)
        else:
            name, value = line.split("=", 1)
            outputs[name] = value
    return outputs


def plan(**overrides):
    with tempfile.TemporaryDirectory() as tmp:
        plan_file, output = os.path.join(tmp, "plan.json"), os.path.join(tmp, "out")
        with redirect_stdout(io.StringIO()):
            status = check.command_plan(plan_args(plan_file=plan_file, github_output=output, **overrides))
        outputs = read_outputs(Path(output).read_text())
        record = json.loads(Path(plan_file).read_text())
        record["outputs"] = outputs
        return status, json.loads(outputs["commands"]), record


class InputTest(unittest.TestCase):
    def test_rejects_bad_values(self):
        cases = {
            "php-version": {"php_version": "8"},
            "static-checks": {"static_checks": "yes"},
            "composer-validate-strict": {"composer_validate_strict": "1"},
            "phpstan-script": {"phpstan_script": "check:stan; rm -rf /"},
            "composer-scripts": {"composer_scripts": "ok, --bad"},
            "test-script": {"test_script": "test\n"},
            "expect-majors": {"expect_majors": "symfony/console"},
        }
        for name, override in cases.items():
            with self.subTest(input=name), self.assertRaisesRegex(check.CheckError, name):
                check.parse_options(plan_args(**override))

    def test_expect_majors_takes_package_and_major_items(self):
        self.assertEqual(
            check.parse_expect_majors("symfony/console:8, symfony/http-kernel:8\npsr/log:3\n"),
            (("symfony/console", 8), ("symfony/http-kernel", 8), ("psr/log", 3)),
        )
        self.assertEqual(check.parse_expect_majors(""), ())

    def test_expect_majors_rejects_bad_items(self):
        for value in ("symfony/*:8", "Symfony/Console:8", "symfony/console:v8", "symfony/console:08",
                      "symfony/console:8.1", "symfony/console 8", "console:8", "symfony/console:8; rm -rf /"):
            with self.subTest(value=value), self.assertRaisesRegex(check.CheckError, "expect-majors"):
                check.parse_expect_majors(value)

    def test_expect_majors_takes_every_composer_name_form(self):
        for name in ("vendor/pkg", "a-b/c_d.e", "a.b_c-d/e--f", "a1/b2", "0/0", "vendor/a.b-c_d--e"):
            with self.subTest(name=name):
                self.assertEqual(check.parse_expect_majors(f"{name}:1"), ((name, 1),))

    def test_expect_majors_rejects_bad_separators(self):
        for name in ("-vendor/pkg", "vendor-/pkg", "ven--dor/pkg", "vendor/pkg---x", "vendor/-pkg",
                     "vendor/pkg-", "vendor/pkg.", "vendor/p..kg", "vendor/pkg.-x", "vendor/p_-kg"):
            with self.subTest(name=name), self.assertRaisesRegex(check.CheckError, "expect-majors"):
                check.parse_expect_majors(f"{name}:1")

    def test_expect_major_pattern_is_linear_on_adversarial_input(self):
        # A nested optional separator made this backtrack exponentially:
        # 24 characters already took about 0.4 s.
        for value in ("0" * 5000 + "!", "a/" + "0" * 5000 + "!", "a-" * 2500 + "/b:", "a/" + "b-" * 2500 + "!"):
            with self.subTest(length=len(value)):
                start = time.perf_counter()
                self.assertIsNone(check.EXPECT_MAJOR_RE.fullmatch(value))
                self.assertLess(time.perf_counter() - start, 0.5)

    def test_expect_majors_rejects_a_package_named_twice(self):
        with self.assertRaisesRegex(check.CheckError, "symfony/console twice"):
            check.parse_expect_majors("symfony/console:8, symfony/console:7")

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

    def test_default_plans_no_expected_majors(self):
        _, _, record = plan()
        self.assertEqual(record["expect-majors"], {})
        self.assertEqual(record["outputs"]["expect-majors"], "{}")

    def test_highest_dry_run_matches_expected_ci_outputs(self):
        _, commands, record = plan(**HIGHEST_INPUTS)
        self.assertEqual(commands, EXPECTED_HIGHEST["commands"])
        self.assertEqual(json.loads(record["outputs"]["expect-majors"]), EXPECTED_HIGHEST["expect-majors"])

    def test_strict_validate_adds_the_flag(self):
        _, commands, _ = plan(composer_validate_strict="true")
        self.assertEqual(commands[0], "composer validate --no-interaction --no-check-publish --no-check-lock --strict")


class ToolCacheTest(unittest.TestCase):
    def test_no_tool_cache_by_default(self):
        _, _, record = plan()
        outputs = record["outputs"]
        self.assertEqual(outputs["tool-cache-paths"], "[]")
        for name in ("tool-cache-path-lines", "tool-cache-prefix"):
            with self.subTest(output=name):
                self.assertEqual(outputs[name], "")
        self.assertIsNone(record["tool-cache"])

    def test_no_tool_cache_without_the_static_checks(self):
        _, _, record = plan(tool_cache_paths=".cache", static_checks="false")
        self.assertEqual(record["outputs"]["tool-cache-prefix"], "")
        self.assertEqual(record["outputs"]["tool-cache-paths"], "[]")
        self.assertIsNone(record["tool-cache"])

    def test_basic_dry_run_matches_expected_ci_outputs(self):
        _, _, record = plan(workspace=str(REPOSITORY), path="php-library-check/tests/fixtures/basic",
                            tool_cache_paths=".cache")
        self.assertEqual(json.loads(record["outputs"]["tool-cache-paths"]), EXPECTED["tool-cache-paths"])

    def test_plans_paths_and_the_key_prefix(self):
        _, _, record = plan(tool_cache_paths=".cache/phpstan\n.cache/rector, .php-cs-fixer.cache",
                            php_version="8.3", dependency_versions="highest")
        outputs = record["outputs"]
        self.assertEqual(json.loads(outputs["tool-cache-paths"]),
                         [".cache/phpstan", ".cache/rector", ".php-cs-fixer.cache"])
        self.assertEqual(outputs["tool-cache-path-lines"], ".cache/phpstan\n.cache/rector\n.php-cs-fixer.cache")
        prefix = outputs["tool-cache-prefix"]
        self.assertRegex(prefix, r"^php-library-check-tools-Linux-php8\.3-highest-[0-9a-f]{12}-[0-9a-f]{64}-$")
        self.assertEqual(record["tool-cache"], {"prefix": prefix, "commit": COMMIT})
        self.assertNotIn("tool-cache-key", outputs)

    def test_prefix_changes_with_os_php_dependency_versions_and_path(self):
        def prefix(**overrides):
            _, _, record = plan(tool_cache_paths=".cache", **overrides)
            return record["outputs"]["tool-cache-prefix"]

        base = prefix()
        for override in ({"runner_os": "macOS"}, {"php_version": "8.4"}, {"dependency_versions": "highest"},
                         {"workspace": str(REPOSITORY), "path": "php-library-check/tests/fixtures/basic"}):
            with self.subTest(override=override):
                self.assertNotEqual(prefix(**override), base)
        self.assertEqual(prefix(commit="f" * 40), base)

    def test_config_hash_follows_composer_and_tool_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp)
            (library / "composer.json").write_text("{}")
            first = check.tool_config_hash(library)
            (library / "src.php").write_text("<?php")
            self.assertEqual(check.tool_config_hash(library), first)
            for name in ("composer.lock", ".phpstan.neon", "phpstan.neon.dist", ".php-rector.php", "rector.php",
                         ".php-cs-fixer.dist.php", ".php-cs-fixer.php"):
                with self.subTest(file=name):
                    before = check.tool_config_hash(library)
                    (library / name).write_text(name)
                    after = check.tool_config_hash(library)
                    self.assertNotEqual(after, before)
                    (library / name).write_text(name + " changed")
                    self.assertNotEqual(check.tool_config_hash(library), after)

    def test_rejects_unsafe_paths(self):
        for value in ("/tmp/cache", "../cache", ".cache/../../x", ".", "./", "vendor", "vendor/bin", "*.cache",
                      ".cache/**", "!.cache", "~/.cache", "$HOME/.cache", ".cache\\x", "src/../vendor/x"):
            with self.subTest(value=value), self.assertRaisesRegex(check.CheckError, "tool-cache-paths"):
                plan(tool_cache_paths=value)

    def test_rejects_a_path_named_twice(self):
        with self.assertRaisesRegex(check.CheckError, "twice"):
            plan(tool_cache_paths=".cache, ./.cache")

    def test_rejects_bad_dependency_versions_runner_os_and_commit(self):
        cases = {"dependency-versions": {"dependency_versions": "lowest"}, "runner OS": {"runner_os": "Linux x"},
                 "commit": {"commit": "main"}}
        for message, override in cases.items():
            with self.subTest(case=message), self.assertRaisesRegex(check.CheckError, message):
                plan(tool_cache_paths=".cache", **override)

    def test_dry_run_prints_the_tool_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                check.command_plan(plan_args(plan_file=os.path.join(tmp, "plan.json"), tool_cache_paths=".cache"))
        self.assertIn("Would restore and save the tool caches .cache, key php-library-check-tools-Linux-php8.2-",
                      buffer.getvalue())
        self.assertIn(f"-<hash of the installed dependencies>-{COMMIT}", buffer.getvalue())

    def test_multi_line_outputs_use_the_delimiter_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "out")
            check.write_outputs(output, {"one": "a", "many": "b\nc"})
            self.assertEqual(read_outputs(Path(output).read_text()), {"one": "a", "many": "b\nc"})


def installed_package(name, version, reference, **extra):
    return {"name": name, "version": version, "source": {"type": "git", "reference": reference},
            "dist": {"type": "zip", "reference": reference}, **extra}


INSTALLED = {
    "packages": [
        installed_package("symfony/console", "v8.1.11", "a" * 40, **{"install-path": "../symfony/console"}),
        installed_package("psr/log", "3.0.2", "b" * 40),
    ],
    "dev": True,
    "dev-package-names": [],
}


def write_installed(vendor, data):
    (vendor / "composer").mkdir(parents=True, exist_ok=True)
    (vendor / "composer" / "installed.json").write_text(json.dumps(data))


class DependenciesHashTest(unittest.TestCase):
    def hash_of(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            write_installed(Path(tmp), data)
            return check.dependencies_hash(Path(tmp) / "composer" / "installed.json")

    def test_same_dependencies_give_the_same_hash(self):
        reordered = {**INSTALLED, "packages": list(reversed(INSTALLED["packages"]))}
        moved = {**INSTALLED, "packages": [{**package, "install-path": "/elsewhere"} for package in INSTALLED["packages"]]}
        base = self.hash_of(INSTALLED)
        self.assertRegex(base, r"^[0-9a-f]{64}$")
        self.assertEqual(self.hash_of(reordered), base)
        self.assertEqual(self.hash_of(moved), base)

    def test_hash_follows_name_version_and_reference(self):
        base = self.hash_of(INSTALLED)
        console, log = INSTALLED["packages"]
        cases = {
            "version": [{**console, "version": "v8.1.12"}, log],
            "reference": [installed_package("symfony/console", "v8.1.11", "c" * 40), log],
            "added": [console, log, installed_package("psr/container", "2.0.2", "d" * 40)],
            "removed": [console],
        }
        for case, packages in cases.items():
            with self.subTest(case=case):
                self.assertNotEqual(self.hash_of({**INSTALLED, "packages": packages}), base)

    def test_dist_reference_is_used_without_a_source(self):
        console, log = INSTALLED["packages"]
        without_source = {key: value for key, value in console.items() if key != "source"}
        self.assertEqual(self.hash_of({**INSTALLED, "packages": [without_source, log]}), self.hash_of(INSTALLED))

    def test_reads_the_composer_1_list_form(self):
        self.assertEqual(self.hash_of(INSTALLED["packages"]), self.hash_of(INSTALLED))

    def test_rejects_a_missing_or_malformed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            installed = Path(tmp) / "installed.json"
            with self.assertRaisesRegex(check.CheckError, "does not exist"):
                check.dependencies_hash(installed)
            for content, message in (("{", "not readable JSON"), ('{"packages": {}}', "no package list"),
                                     ("3", "no package list")):
                installed.write_text(content)
                with self.subTest(content=content), self.assertRaisesRegex(check.CheckError, message):
                    check.dependencies_hash(installed)


class CacheKeyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vendor = self.root / "vendor"
        self.plan_file = self.root / "plan.json"
        self.output = self.root / "out"

    def cache_key(self, tool_cache, vendor_stdout=None, returncode=0):
        self.plan_file.write_text(json.dumps({"directory": str(self.root), "commands": [], "tool-cache": tool_cache}))
        stdout = f"{self.vendor}\n" if vendor_stdout is None else vendor_stdout
        completed = subprocess.CompletedProcess(list(check.VENDOR_DIR_COMMAND), returncode, stdout=stdout, stderr="")
        buffer = io.StringIO()
        with mock.patch.object(check.subprocess, "run", return_value=completed) as run, redirect_stdout(buffer):
            status = check.main(["cache-key", f"--plan-file={self.plan_file}", f"--github-output={self.output}"])
        self.assertEqual(status, 0)
        return read_outputs(self.output.read_text()), buffer.getvalue(), run

    def test_key_is_prefix_dependencies_hash_and_commit(self):
        write_installed(self.vendor, INSTALLED)
        outputs, printed, run = self.cache_key({"prefix": "php-library-check-tools-Linux-php8.4-highest-x-y-",
                                                "commit": COMMIT})
        dependencies = check.dependencies_hash(self.vendor / "composer" / "installed.json")
        restore_key = f"php-library-check-tools-Linux-php8.4-highest-x-y-{dependencies}-"
        self.assertEqual(outputs, {"key": restore_key + COMMIT, "restore-keys": restore_key})
        self.assertIn(restore_key + COMMIT, printed)
        run.assert_called_once_with(list(check.VENDOR_DIR_COMMAND), cwd=str(self.root), check=False,
                                    capture_output=True, text=True)

    def test_other_dependencies_give_another_restore_key(self):
        tool_cache = {"prefix": "p-", "commit": COMMIT}
        write_installed(self.vendor, INSTALLED)
        first, _, _ = self.cache_key(tool_cache)
        write_installed(self.vendor, {**INSTALLED, "packages": INSTALLED["packages"][:1]})
        second, _, _ = self.cache_key(tool_cache)
        self.assertNotEqual(first["restore-keys"], second["restore-keys"])
        self.assertTrue(second["key"].startswith(second["restore-keys"]))

    def test_uses_the_last_line_of_the_vendor_dir_output(self):
        write_installed(self.vendor, INSTALLED)
        outputs, _, _ = self.cache_key({"prefix": "p-", "commit": COMMIT}, vendor_stdout=f"notice\n{self.vendor}\n")
        self.assertTrue(outputs["key"].startswith("p-"))

    def test_unreadable_dependencies_skip_the_cache_with_a_warning(self):
        cases = {
            "no installed.json": {},
            "composer config fails": {"returncode": 1},
            "relative vendor dir": {"vendor_stdout": "vendor\n"},
            "empty output": {"vendor_stdout": ""},
        }
        for case, overrides in cases.items():
            with self.subTest(case=case):
                outputs, printed, _ = self.cache_key({"prefix": "p-", "commit": COMMIT}, **overrides)
                self.assertEqual(outputs, {"key": "", "restore-keys": ""})
                self.assertIn("::warning::No tool cache in this job:", printed)
                self.output.unlink()

    def test_no_planned_cache_writes_empty_outputs_without_composer(self):
        outputs, printed, run = self.cache_key(None)
        self.assertEqual(outputs, {"key": "", "restore-keys": ""})
        self.assertEqual(printed, "")
        run.assert_not_called()


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


def show_output(**versions):
    installed = [{"name": name.replace("__", "/").replace("_", "-"), "version": version}
                 for name, version in versions.items()]
    return json.dumps({"installed": installed})


class MajorCheckTest(unittest.TestCase):
    def test_reports_missing_and_other_majors(self):
        installed = {"symfony/console": "v7.4.23", "symfony/process": "v8.1.11", "symfony/yaml": "8.1.x-dev"}
        expected = {"symfony/console": 8, "symfony/process": 8, "symfony/yaml": 8, "symfony/finder": 8}
        self.assertEqual(
            check.major_mismatches(expected, installed),
            [
                "symfony/console v7.4.23 is installed, expected major version 8",
                "symfony/finder is not installed (expected major version 8)",
            ],
        )

    def test_a_branch_without_a_version_is_a_mismatch(self):
        self.assertEqual(
            check.major_mismatches({"acme/lib": 1}, {"acme/lib": "dev-main"}),
            ["acme/lib dev-main is installed, expected major version 1"],
        )

    def run_plan(self, expect_majors, show_stdout, show_status=0):
        with tempfile.TemporaryDirectory() as tmp:
            plan_file = os.path.join(tmp, "plan.json")
            with redirect_stdout(io.StringIO()):
                check.command_plan(plan_args(plan_file=plan_file, dry_run="false", expect_majors=expect_majors,
                                             composer_validate="false", strict_psr="false", lint_script="",
                                             phpstan_script="", cs_fixer_script="", rector_script="",
                                             composer_scripts=""))
            calls, show_command = [], list(check.SHOW_COMMAND)

            def fake_run(command, cwd, check=False, env=None, capture_output=False, text=False):
                calls.append(command)
                if command == show_command:
                    return subprocess.CompletedProcess(command, show_status, stdout=show_stdout, stderr="")
                return subprocess.CompletedProcess(command, 0)

            buffer = io.StringIO()
            with mock.patch.object(check.subprocess, "run", fake_run), redirect_stdout(buffer):
                status = check.main(["run", "--plan-file", plan_file])
        return status, calls, buffer.getvalue()

    def test_a_mismatch_fails_before_any_check(self):
        status, calls, printed = self.run_plan("symfony/console:8", show_output(symfony__console="v7.4.23"))
        self.assertEqual(status, 1)
        self.assertEqual(calls, [list(check.SHOW_COMMAND)])
        self.assertIn("::error::expect-majors: symfony/console v7.4.23 is installed, expected major version 8", printed)

    def test_matching_majors_run_the_checks(self):
        status, calls, printed = self.run_plan("symfony/console:8", show_output(symfony__console="v8.1.11"))
        self.assertEqual(status, 0)
        self.assertEqual(calls, [list(check.SHOW_COMMAND), ["composer", "run-script", "--no-interaction", "test"]])
        self.assertIn("symfony/console v8.1.11: major version 8 as expected.", printed)

    def test_failed_composer_show_fails_the_job(self):
        status, calls, printed = self.run_plan("symfony/console:8", "", show_status=1)
        self.assertEqual(status, 1)
        self.assertEqual(len(calls), 1)
        self.assertIn("composer show --format=json --no-interaction failed with exit code 1", printed)

    def test_no_installed_package_is_a_mismatch(self):
        status, calls, printed = self.run_plan("symfony/console:8", "[]")
        self.assertEqual(status, 1)
        self.assertEqual(len(calls), 1)
        self.assertIn("symfony/console is not installed (expected major version 8)", printed)

    def test_output_that_is_not_json_fails_the_job(self):
        status, _, printed = self.run_plan("symfony/console:8", "Warning: something")
        self.assertEqual(status, 1)
        self.assertIn("did not print JSON", printed)

    def test_no_expected_majors_runs_no_composer_show(self):
        status, calls, _ = self.run_plan("", "")
        self.assertEqual(status, 0)
        self.assertEqual(calls, [["composer", "run-script", "--no-interaction", "test"]])


if __name__ == "__main__":
    unittest.main()
