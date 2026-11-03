"""Tests of scripts/plan.py. No network: the deployed hash is simulated."""

from __future__ import annotations

import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPONENT = HERE.parent
REPOSITORY = COMPONENT.parent
FIXTURE = HERE / "fixtures" / "basic" / "site"
EXPECTED = HERE / "fixtures" / "expected" / "basic.env"
FIXTURE_DOCS_PATH = "cloudflare-pages-deploy/tests/fixtures/basic/site"
ACCOUNT_ID = "0123456789abcdef0123456789abcdef"

spec = importlib.util.spec_from_file_location("plan", COMPONENT / "scripts" / "plan.py")
plan = importlib.util.module_from_spec(spec)
sys.modules["plan"] = plan
spec.loader.exec_module(plan)


def arguments(**overrides: str) -> list[str]:
    values = {
        "docs-path": "site",
        "project-name": "example-docs",
        "account-id": ACCOUNT_ID,
        "has-api-token": "true",
    }
    values.update({key.replace("_", "-"): value for key, value in overrides.items()})
    return [f"--{key}={value}" for key, value in values.items()]


def no_fetch(url: str) -> str:
    raise AssertionError(f"unexpected fetch of {url}")


class SiteTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.workspace)
        shutil.copytree(FIXTURE, self.workspace / "site")

    def plan(self, fetch=no_fetch, **overrides: str):
        with redirect_stdout(io.StringIO()):
            return plan.build_plan(plan.parse_args(arguments(**overrides)), self.workspace, fetch)

    def hash_of_site(self) -> str:
        return plan.content_hash(self.workspace / "site", set())


class ExpectedFixtureTest(unittest.TestCase):
    def test_dry_run_of_the_fixture_matches_the_expected_outputs(self) -> None:
        args = plan.parse_args(
            [f"--docs-path={FIXTURE_DOCS_PATH}", "--project-name=example-docs", "--dry-run=true"]
        )
        with redirect_stdout(io.StringIO()):
            result = plan.build_plan(args, REPOSITORY, no_fetch)
        actual = "".join(f"{key}={value}\n" for key, value in result.outputs())
        self.assertEqual(EXPECTED.read_text(encoding="utf-8"), actual)


class DecisionTest(SiteTestCase):
    def test_deploys_when_no_hash_is_deployed(self) -> None:
        self.assertTrue(self.plan(fetch=lambda url: "").deploy)

    def test_skips_when_the_deployed_hash_matches(self) -> None:
        local = self.hash_of_site()
        self.assertFalse(self.plan(fetch=lambda url: local).deploy)

    def test_deploys_when_the_deployed_hash_differs(self) -> None:
        self.assertTrue(self.plan(fetch=lambda url: "0" * 64).deploy)

    def test_force_deploys_without_fetching(self) -> None:
        self.assertTrue(self.plan(force="true").deploy)

    def test_dry_run_never_deploys_nor_fetches(self) -> None:
        result = self.plan(dry_run="true", force="true", account_id="", has_api_token="false")
        self.assertFalse(result.deploy)

    def test_fetches_the_hash_from_the_default_pages_url(self) -> None:
        seen = []
        self.plan(fetch=lambda url: seen.append(url) or "")
        self.assertEqual(["https://example-docs.pages.dev"], seen)

    def test_fetches_the_hash_from_the_given_url_without_trailing_slash(self) -> None:
        seen = []
        self.plan(fetch=lambda url: seen.append(url) or "", deploy_url="https://docs.example.com/")
        self.assertEqual(["https://docs.example.com"], seen)


class PathTest(SiteTestCase):
    def test_default_dist_and_build_directory_follow_docs_path(self) -> None:
        result = self.plan(dry_run="true", docs_path="site/")
        self.assertEqual("site/.vitepress/dist", result.dist_path)
        self.assertEqual("site", result.build_directory)
        self.assertEqual("pages deploy site/.vitepress/dist --project-name=example-docs", result.command)

    def test_repository_root_as_docs_path(self) -> None:
        result = self.plan(dry_run="true", docs_path=".")
        self.assertEqual(".vitepress/dist", result.dist_path)
        self.assertEqual(".", result.build_directory)

    def test_explicit_build_directory_and_dist_path(self) -> None:
        result = self.plan(dry_run="true", build_working_directory=".", dist_path="site/out")
        self.assertEqual(".", result.build_directory)
        self.assertEqual("pages deploy site/out --project-name=example-docs", result.command)

    def test_rejects_unsafe_paths(self) -> None:
        for name, value in (
            ("docs_path", "../site"),
            ("docs_path", "/site"),
            ("docs_path", "site;rm"),
            ("dist_path", "site/dist --branch=x"),
            ("build_working_directory", "$(id)"),
            ("cache_dependency_path", "a b/package-lock.json"),
        ):
            with self.subTest(name=name, value=value), self.assertRaises(plan.InputError):
                self.plan(dry_run="true", **{name: value})

    def test_rejects_a_missing_docs_directory(self) -> None:
        with self.assertRaisesRegex(plan.InputError, "not a directory"):
            self.plan(dry_run="true", docs_path="missing")


class InputTest(SiteTestCase):
    def test_missing_project_name_names_the_variable(self) -> None:
        with self.assertRaisesRegex(plan.InputError, "CLOUDFLARE_PAGES_DEPLOY_PROJECT_NAME"):
            self.plan(dry_run="true", project_name="")

    def test_missing_account_id_names_the_variable(self) -> None:
        with self.assertRaisesRegex(plan.InputError, "CLOUDFLARE_PAGES_DEPLOY_ACCOUNT_ID"):
            self.plan(account_id="")

    def test_missing_api_token_fails_outside_a_dry_run(self) -> None:
        with self.assertRaisesRegex(plan.InputError, "api-token"):
            self.plan(has_api_token="false")

    def test_rejects_invalid_values(self) -> None:
        for name, value in (
            ("project_name", "Example_Docs"),
            ("project_name", "-docs"),
            ("project_name", "a" * 59),
            ("account_id", "not-an-account"),
            ("deploy_url", "http://example-docs.pages.dev"),
            ("deploy_url", "https://example.com/?a=b"),
            ("build_command", "   "),
            ("build_command", "npm run a\nnpm run b"),
            ("node_version", "22; id"),
            ("force", "yes"),
            ("dry_run", "1"),
        ):
            with self.subTest(name=name, value=value), self.assertRaises(plan.InputError):
                self.plan(fetch=lambda url: "", **{name: value})

    def test_accepts_a_valid_account_id_in_a_dry_run(self) -> None:
        self.assertFalse(self.plan(dry_run="true").deploy)


class ContentHashTest(SiteTestCase):
    def test_any_source_change_changes_the_hash(self) -> None:
        before = self.hash_of_site()
        (self.workspace / "site" / "guide" / "start.md").write_text("changed\n", encoding="utf-8")
        self.assertNotEqual(before, self.hash_of_site())

    def test_a_new_asset_changes_the_hash(self) -> None:
        before = self.hash_of_site()
        (self.workspace / "site" / "public").mkdir()
        (self.workspace / "site" / "public" / "logo.svg").write_text("<svg/>", encoding="utf-8")
        self.assertNotEqual(before, self.hash_of_site())

    def test_a_renamed_file_changes_the_hash(self) -> None:
        before = self.hash_of_site()
        site = self.workspace / "site"
        (site / "guide" / "start.md").rename(site / "guide" / "begin.md")
        self.assertNotEqual(before, self.hash_of_site())

    def test_build_outputs_and_dependencies_do_not_change_the_hash(self) -> None:
        before = self.hash_of_site()
        site = self.workspace / "site"
        for directory in ("node_modules/pkg", ".vitepress/dist", ".vitepress/cache", ".git"):
            (site / directory).mkdir(parents=True)
            (site / directory / "file.txt").write_text("ignored", encoding="utf-8")
        self.assertEqual(before, self.hash_of_site())

    def test_a_custom_dist_path_inside_the_site_does_not_change_the_hash(self) -> None:
        before = self.plan(dry_run="true", dist_path="site/out").content_hash
        (self.workspace / "site" / "out").mkdir()
        (self.workspace / "site" / "out" / "index.html").write_text("<html/>", encoding="utf-8")
        self.assertEqual(before, self.plan(dry_run="true", dist_path="site/out").content_hash)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks not supported")
    def test_a_symlink_counts_by_its_target_not_its_content(self) -> None:
        site = self.workspace / "site"
        os.symlink("/etc/hostname", site / "link.md")
        self.assertEqual("link:/etc/hostname", plan.file_digest(site / "link.md"))


class MainTest(SiteTestCase):
    def test_main_writes_the_outputs_and_fails_on_invalid_input(self) -> None:
        output = self.workspace / "github-output"
        cwd = Path.cwd()
        os.chdir(self.workspace)
        self.addCleanup(os.chdir, cwd)
        environment = dict(os.environ, GITHUB_OUTPUT=str(output))
        with unittest.mock.patch.dict(os.environ, environment, clear=True), redirect_stdout(io.StringIO()) as log:
            self.assertEqual(0, plan.main(arguments(dry_run="true")))
            self.assertEqual(1, plan.main(arguments(project_name="Bad Name")))
        written = output.read_text(encoding="utf-8")
        self.assertIn("deploy=false\n", written)
        self.assertIn("command=pages deploy site/.vitepress/dist --project-name=example-docs\n", written)
        self.assertIn("::error::input project-name", log.getvalue())


if __name__ == "__main__":
    unittest.main()
