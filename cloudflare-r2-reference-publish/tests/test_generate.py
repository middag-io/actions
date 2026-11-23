"""Tests of scripts/generate.py: input validation, version resolution, the
generator commands and the package manifest.

The generators are not run here: the commands are built and checked as
lists; the CI dry-run jobs run both generators on the fixtures.

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
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
FIXTURES = os.path.join(HERE, "fixtures")


def load(name):
    spec = importlib.util.spec_from_file_location(f"r2_reference_{name}", os.path.join(SCRIPTS, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generate = load("generate")


def plan_args(workspace, **overrides):
    values = {
        "workspace": workspace,
        "generator": "phpdocumentor",
        "package_name": "example-library",
        "version": "",
        "ref_name": "v1.2.0",
        "working_directory": "php-basic",
        "output_path": ".docs-dist",
        "config": "",
        "source_paths": "src",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class WorkspaceTestCase(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.workspace)
        for name in ("php-basic", "typedoc-basic"):
            shutil.copytree(os.path.join(FIXTURES, name), os.path.join(self.workspace, name))


class ResolveVersionTest(unittest.TestCase):
    def test_explicit_version_wins_over_the_tag(self):
        self.assertEqual(generate.resolve_version("v2.0.0", "v1.2.0"), "2.0.0")

    def test_reads_plain_v_and_component_tags(self):
        for ref, expected in (
            ("1.2.3", "1.2.3"),
            ("v1.2.3", "1.2.3"),
            ("example-library-v1.2.3", "1.2.3"),
            ("v2.0.0-rc.1", "2.0.0-rc.1"),
        ):
            with self.subTest(ref=ref):
                self.assertEqual(generate.resolve_version("", ref), expected)

    def test_fails_without_version_and_tag(self):
        with self.assertRaisesRegex(generate.InputError, "not on a tag"):
            generate.resolve_version("", "")

    def test_fails_on_a_branch_name(self):
        with self.assertRaisesRegex(generate.InputError, "no release version"):
            generate.resolve_version("", "main")


class CheckVisibilityTest(unittest.TestCase):
    def test_accepts_a_public_repository(self):
        generate.check_visibility("public", "false")

    def test_rejects_private_internal_and_unknown(self):
        for visibility, private in (
            ("private", "true"),
            ("internal", "true"),
            ("internal", "false"),
            ("public", "true"),
            ("public", ""),
            ("", "false"),
            ("", ""),
        ):
            with self.subTest(visibility=visibility, private=private), \
                    self.assertRaisesRegex(generate.InputError, "not public"):
                generate.check_visibility(visibility, private)

    def test_main_fails_with_an_annotation(self):
        argv = ["visibility", "--visibility=private", "--private=true"]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(generate.main(argv), 1)
        self.assertIn("::error::the calling repository is not public", out.getvalue())


class ValidatePathTest(unittest.TestCase):
    def test_normalizes_dot_segments(self):
        self.assertEqual(generate.validate_relative_path("p", "./a//b/"), "a/b")
        self.assertEqual(generate.validate_relative_path("p", "."), ".")

    def test_rejects_absolute_parent_and_backslash(self):
        for value in ("/etc", "../x", "a/../b", "a\\b", "a b"):
            with self.subTest(value=value), self.assertRaises(generate.InputError):
                generate.validate_relative_path("p", value)

    def test_empty_only_when_allowed(self):
        self.assertEqual(generate.validate_relative_path("p", "", allow_empty=True), "")
        with self.assertRaises(generate.InputError):
            generate.validate_relative_path("p", "")


class PlanTest(WorkspaceTestCase):
    def test_plans_a_phpdocumentor_package(self):
        outputs = generate.plan(plan_args(self.workspace))
        self.assertEqual(outputs["version"], "1.2.0")
        self.assertEqual(outputs["package-path"], ".docs-dist")
        self.assertEqual(outputs["xref-path"], ".docs-dist/xref/1.2.0")
        self.assertEqual(outputs["source-paths"], "src")

    def test_typedoc_needs_the_lockfile(self):
        args = plan_args(self.workspace, generator="typedoc", working_directory="php-basic", source_paths="")
        with self.assertRaisesRegex(generate.InputError, "package-lock.json"):
            generate.plan(args)
        args = plan_args(self.workspace, generator="typedoc", working_directory="typedoc-basic", source_paths="")
        self.assertEqual(generate.plan(args)["generator"], "typedoc")

    def test_rejects_unknown_generator_and_bad_package_name(self):
        with self.assertRaisesRegex(generate.InputError, "generator"):
            generate.plan(plan_args(self.workspace, generator="doctum"))
        with self.assertRaisesRegex(generate.InputError, "package-name"):
            generate.plan(plan_args(self.workspace, package_name="a/b"))

    def test_rejects_missing_paths(self):
        with self.assertRaisesRegex(generate.InputError, "working-directory"):
            generate.plan(plan_args(self.workspace, working_directory="absent"))
        with self.assertRaisesRegex(generate.InputError, "source path"):
            generate.plan(plan_args(self.workspace, source_paths="src,lib"))
        with self.assertRaisesRegex(generate.InputError, "config"):
            generate.plan(plan_args(self.workspace, config="phpdoc.dist.xml"))

    def test_rejects_a_non_empty_output_path(self):
        os.makedirs(os.path.join(self.workspace, ".docs-dist"))
        self.assertEqual(generate.plan(plan_args(self.workspace))["version"], "1.2.0")
        with open(os.path.join(self.workspace, ".docs-dist", "stale"), "w", encoding="utf-8"):
            pass
        with self.assertRaisesRegex(generate.InputError, "output-path"):
            generate.plan(plan_args(self.workspace))


class CommandTest(unittest.TestCase):
    def test_phpdocumentor_runs_the_pinned_image_offline(self):
        command = generate.phpdocumentor_command(
            "/w", "lib", ".docs-dist/xref/1.2.0", "phpdoc.dist.xml", ["src", "inc"], 1001, 121
        )
        self.assertEqual(command[:4], ["docker", "run", "--rm", "--network=none"])
        self.assertIn("--user=1001:121", command)
        self.assertIn("--volume=/w:/data", command)
        self.assertIn("--workdir=/data/lib", command)
        self.assertIn(generate.PHPDOC_IMAGE, command)
        self.assertRegex(generate.PHPDOC_IMAGE, r"^phpdoc/phpdoc:[0-9.]+@sha256:[0-9a-f]{64}$")
        self.assertIn("--target=/data/.docs-dist/xref/1.2.0", command)
        self.assertIn("--config=phpdoc.dist.xml", command)
        self.assertEqual(command[-2:], ["--directory=src", "--directory=inc"])

    def test_phpdocumentor_at_the_workspace_root(self):
        command = generate.phpdocumentor_command("/w", ".", "out/xref/1.0.0", "", [], 1, 1)
        self.assertIn("--workdir=/data", command)
        self.assertFalse(any(item.startswith("--config") for item in command))

    def test_typedoc_installs_from_the_lockfile_and_never_downloads_typedoc(self):
        install, run = generate.typedoc_commands("/w", ".docs-dist/xref/1.2.0", "typedoc.json", ["src/index.ts"])
        self.assertEqual(install[:2], ["npm", "ci"])
        self.assertEqual(run[:3], ["npx", "--no-install", "typedoc"])
        self.assertEqual(run[3:5], ["--out", "/w/.docs-dist/xref/1.2.0"])
        self.assertEqual(run[5:], ["--options", "typedoc.json", "src/index.ts"])

    def test_build_stops_at_the_first_failure(self):
        args = SimpleNamespace(
            workspace=tempfile.mkdtemp(), generator="typedoc", working_directory=".",
            xref_path="out/xref/1.0.0", config="", source_paths="",
        )
        self.addCleanup(shutil.rmtree, args.workspace)
        failed = SimpleNamespace(returncode=2)
        with mock.patch.object(generate.subprocess, "run", return_value=failed) as run, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(generate.build(args), 2)
        self.assertEqual(run.call_count, 1)
        self.assertIn("::error::npm ci failed", out.getvalue())


class ManifestTest(WorkspaceTestCase):
    def args(self):
        return SimpleNamespace(
            workspace=self.workspace, package_name="example-library", version="1.2.0",
            package_path=".docs-dist", xref_path=".docs-dist/xref/1.2.0",
        )

    def test_writes_the_manifest_after_checking_the_entry_page(self):
        xref = os.path.join(self.workspace, ".docs-dist", "xref", "1.2.0")
        os.makedirs(os.path.join(xref, "css"))
        for name in ("index.html", "css/base.css"):
            with open(os.path.join(xref, name), "w", encoding="utf-8") as handle:
                handle.write("x")
        self.assertEqual(generate.write_manifest(self.args()), 2)
        with open(os.path.join(self.workspace, ".docs-dist", "manifest.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual(manifest, {
            "package": "example-library",
            "latest": "1.2.0",
            "versions": {"1.2.0": {"has_reference": True}},
        })

    def test_fails_without_an_entry_page(self):
        os.makedirs(os.path.join(self.workspace, ".docs-dist", "xref", "1.2.0"))
        with self.assertRaisesRegex(generate.InputError, "index.html"):
            generate.write_manifest(self.args())


class MainTest(WorkspaceTestCase):
    def test_plan_writes_the_step_outputs(self):
        output = os.path.join(self.workspace, "github-output")
        argv = [
            "plan", f"--workspace={self.workspace}", "--generator=phpdocumentor",
            "--package-name=example-library", "--ref-name=example-library-v1.2.0",
            "--working-directory=php-basic", "--source-paths=src",
        ]
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": output}):
            self.assertEqual(generate.main(argv), 0)
        with open(output, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("version=1.2.0\n", text)
        self.assertIn("xref-path=.docs-dist/xref/1.2.0\n", text)

    def test_input_error_is_an_annotation_and_exit_status_1(self):
        argv = ["plan", f"--workspace={self.workspace}", "--generator=sami", "--package-name=x"]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(generate.main(argv), 1)
        self.assertIn("::error::generator must be one of", out.getvalue())


if __name__ == "__main__":
    unittest.main()
