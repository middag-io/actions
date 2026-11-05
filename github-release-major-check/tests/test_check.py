"""Tests of scripts/check.py: the Release-As rules, the breaking markers and the run.

git is replaced by a fake: no repository is needed.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import argparse
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import check  # noqa: E402

FIXTURE = HERE / "fixtures" / "basic"
BASE = "a" * 40
HEAD = "b" * 40


def commit(sha, message, parents=1):
    return f"{sha}\x00{' '.join(['c' * 40] * parents)}\x00{message}\n\x1e\n"


def fake_git(version, commits=(), package="."):
    """Returns a git stand-in: show gives the manifest, log gives the commits."""
    calls = []

    def git(*args):
        calls.append(args)
        if args[0] == "show":
            return '{"%s": "%s"}' % (package, version)
        if args[0] == "log":
            return "".join(commits)
        raise AssertionError(f"unexpected git call {args}")

    git.calls = calls
    return git


def args(**overrides):
    values = {
        "manifest_file": ".release-please-manifest.json", "package": ".",
        "title": "fix: correct a typo", "base_sha": BASE, "head_sha": HEAD,
        "dry_run": "false", "workspace": ".", "github_output": "",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def run(version, commits=(), package=".", **overrides):
    """Returns (status, stdout, outputs) of one check."""
    with tempfile.TemporaryDirectory() as tmp:
        output = os.path.join(tmp, "out")
        out = io.StringIO()
        with redirect_stdout(out):
            status = check.check(args(github_output=output, package=package, **overrides),
                                 git=fake_git(version, commits, package))
        outputs = dict(line.split("=", 1) for line in Path(output).read_text().splitlines())
    return status, out.getvalue(), outputs


def sha(digit):
    return str(digit) * 40


class VersionTest(unittest.TestCase):
    def above(self, version, base):
        return check.version_above(check.parse_version("v", version), check.parse_version("b", base))

    def test_precedence(self):
        self.assertTrue(self.above("2.0.1", "2.0.0"))
        self.assertTrue(self.above("2.1.0", "2.0.9"))
        self.assertFalse(self.above("2.0.0", "2.0.0"))
        self.assertFalse(self.above("1.9.9", "2.0.0"))
        self.assertTrue(self.above("2.0.0", "2.0.0-rc.1"))
        self.assertFalse(self.above("2.0.0-rc.1", "2.0.0"))
        self.assertTrue(self.above("2.0.0-rc.2", "2.0.0-rc.1"))
        self.assertFalse(self.above("2.0.0-rc.1", "2.0.0-rc.1"))

    def test_rejects_bad_versions(self):
        for value in ("2", "2.0", "v2.0.0", "2.0.0.0"):
            with self.subTest(value=value), self.assertRaises(check.CheckError):
                check.parse_version("version", value)


class MessageTest(unittest.TestCase):
    def test_breaking_headers(self):
        cases = {
            "feat: add a thing": False,
            "feat(api)!: drop a thing": True,
            "fix!: change a default": True,
            "feat (api) ! : drop a thing": True,
            "FEAT!:x": True,
            "chore: update a pin": False,
            'Revert "feat!: drop a thing"': False,
            "a plain sentence!": False,
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(check.is_breaking(message), expected)

    def test_breaking_footers(self):
        self.assertTrue(check.is_breaking("fix: a\n\nBREAKING CHANGE: the input x is gone"))
        self.assertTrue(check.is_breaking("fix: a\n\nBREAKING-CHANGE: the input x is gone"))
        self.assertTrue(check.is_breaking("fix: a\n\n  BREAKING CHANGE : x"))
        self.assertFalse(check.is_breaking("fix: a\n\nThis is not a BREAKING CHANGE: prose"))
        self.assertFalse(check.is_breaking("BREAKING CHANGE: only a header"))

    def test_release_as_footers(self):
        self.assertEqual(check.release_as_versions("fix: x\n\nRelease-As: 3.0.0"), ["3.0.0"])
        self.assertEqual(check.release_as_versions("fix: x\n\nrelease-as:2.1.0"), ["2.1.0"])
        self.assertEqual(check.release_as_versions("fix: x\n\nRELEASE AS : v2.1.0"), ["v2.1.0"])
        self.assertEqual(check.release_as_versions("fix: x\n\nRelease-As:"), [""])
        self.assertEqual(check.release_as_versions("fix: x\n\nWe never use a Release-As: here"), [])
        self.assertEqual(check.release_as_versions("Release-As: 3.0.0"), [])


class ClassifyTest(unittest.TestCase):
    def kind(self, text, base):
        return check.classify_release_as(text, check.parse_version("b", base))

    def test_major_minor_patch_and_problems(self):
        cases = [
            ("3.0.0", "2.4.1", "major"),
            ("v3.0.0", "2.4.1", "major"),
            ("4.0.0", "2.4.1", "major"),  # skipping a MAJOR is still explicit
            ("1.0.0", "0.9.3", "major"),
            ("2.5.0", "2.4.1", "minor-patch"),
            ("2.4.2", "2.4.1", "minor-patch"),
            ("1.0.0", "1.0.0-rc.3", "minor-patch"),
        ]
        for text, base, expected in cases:
            with self.subTest(text=text, base=base):
                self.assertEqual(self.kind(text, base), (expected, None))

    def test_problems(self):
        cases = [
            ("3.1.0", "2.4.1", "a new MAJOR is released as exactly 3.0.0"),
            ("3.0.1", "2.4.1", "exactly 3.0.0"),
            ("3.0.0-rc.1", "2.4.1", "exactly 3.0.0"),
            ("2.4.1", "2.4.1", "not above the base version 2.4.1"),
            ("2.3.0", "2.4.1", "not above"),
            ("1.9.0", "2.4.1", "not above"),
            ("", "2.4.1", "not a SemVer version (empty)"),
            ("next", "2.4.1", "not a SemVer version (next)"),
            ("3.0", "2.4.1", "not a SemVer version"),
        ]
        for text, base, expected in cases:
            with self.subTest(text=text, base=base):
                kind, problem = self.kind(text, base)
                self.assertIsNone(kind)
                self.assertIn(expected, problem)


class CheckTest(unittest.TestCase):
    def test_compatible_changes_pass(self):
        commits = [commit(sha(1), "feat(api): add a thing"), commit(sha(2), "fix: correct a typo"),
                   commit(sha(3), 'Revert "feat!: drop a thing"')]
        status, _, outputs = run("2.4.1", title="feat(api): add a thing", commits=commits)
        self.assertEqual((status, outputs["result"], outputs["release-as-major"]), (0, "pass", ""))

    def test_bang_in_the_title_fails_without_release_as(self):
        status, out, outputs = run("2.4.1", title="feat(api)!: drop a thing")
        self.assertEqual((status, outputs["result"], outputs["findings"]), (1, "fail", "1"))
        self.assertIn('::error::pull request title: a breaking change', out)
        self.assertIn('"Release-As: 3.0.0"', out)
        self.assertIn("never rebase or force-push", out)

    def test_bang_or_footer_in_a_commit_fails_without_release_as(self):
        for message in ("refactor!: drop x", "fix: rename x\n\nBREAKING CHANGE: x is now y",
                        "fix: rename x\n\nBREAKING-CHANGE: x is now y"):
            with self.subTest(message=message):
                status, out, _ = run("2.4.1", commits=[commit("1234567" + "0" * 33, message)])
                self.assertEqual(status, 1)
                self.assertIn("commit 1234567 (", out)

    def test_explicit_major_allows_breaking_changes(self):
        commits = [commit(sha(1), "feat(api)!: drop a thing"),
                   commit(sha(2), "fix: x\n\nBREAKING CHANGE: y"),
                   commit(sha(3), "chore: release the next major\n\nRelease-As: 3.0.0")]
        status, _, outputs = run("2.4.1", title="feat(api)!: drop a thing", commits=commits)
        self.assertEqual((status, outputs["result"], outputs["release-as-major"]), (0, "pass", "3.0.0"))

    def test_explicit_major_alone_passes(self):
        status, _, outputs = run("0.9.3", commits=[commit(sha(1), "chore: first stable\n\nRelease-As: 1.0.0")])
        self.assertEqual((status, outputs["release-as-major"]), (0, "1.0.0"))

    def test_breaking_change_on_0x_needs_release_as_too(self):
        status, out, _ = run("0.9.3", title="feat!: drop a thing")
        self.assertEqual(status, 1)
        self.assertIn('"Release-As: 1.0.0"', out)

    def test_minor_or_patch_release_as_does_not_allow_breaking_changes(self):
        commits = [commit(sha(1), "feat!: drop a thing\n\nRelease-As: 2.5.0")]
        status, out, _ = run("2.4.1", commits=commits)
        self.assertEqual(status, 1)
        self.assertIn("a breaking change", out)

    def test_minor_or_patch_release_as_passes(self):
        for footer in ("Release-As: 2.5.0", "release-as: 2.4.2"):
            with self.subTest(footer=footer):
                status, _, outputs = run("2.4.1", commits=[commit(sha(1), f"fix: x\n\n{footer}")])
                self.assertEqual((status, outputs["release-as-major"]), (0, ""))

    def test_release_as_raising_the_major_another_way_fails(self):
        for footer in ("Release-As: 3.1.0", "Release-As: 3.0.0-rc.1"):
            with self.subTest(footer=footer):
                status, out, _ = run("2.4.1", commits=[commit(sha(1), f"fix: x\n\n{footer}")])
                self.assertEqual(status, 1)
                self.assertIn("exactly 3.0.0", out)

    def test_release_as_going_back_fails(self):
        status, out, _ = run("2.4.1", commits=[commit(sha(1), "fix: x\n\nRelease-As: 1.9.0")])
        self.assertEqual(status, 1)
        self.assertIn("not above the base version 2.4.1", out)

    def test_two_different_majors_fail(self):
        commits = [commit(sha(1), "chore: a\n\nRelease-As: 3.0.0"), commit(sha(2), "chore: b\n\nRelease-As: 4.0.0")]
        status, out, _ = run("2.4.1", commits=commits)
        self.assertEqual(status, 1)
        self.assertIn("more than one MAJOR", out)

    def test_merge_commits_are_skipped(self):
        merge = commit(sha(1), "Merge branch 'main' into feat/x\n\nfeat!: from main", parents=2)
        status, out, _ = run("2.0.0", commits=[merge, commit(sha(2), "fix: a")])
        self.assertEqual(status, 0)
        self.assertIn("2 message(s) checked", out)

    def test_release_as_in_a_merge_commit_does_not_count(self):
        merge = commit(sha(1), "Merge branch 'main'\n\nRelease-As: 3.0.0", parents=2)
        status, _, _ = run("2.4.1", title="feat!: drop a thing", commits=[merge])
        self.assertEqual(status, 1)

    def test_monorepo_package(self):
        status, out, outputs = run("1.2.0", package="packages/api", title="feat!: drop a thing")
        self.assertEqual((status, outputs["version"]), (1, "1.2.0"))
        self.assertIn("of 'packages/api'", out)

    def test_reads_the_manifest_and_the_range_from_the_given_shas(self):
        git = fake_git("2.0.0")
        with redirect_stdout(io.StringIO()):
            check.check(args(), git=git)
        self.assertEqual(git.calls[0], ("show", f"{BASE}:.release-please-manifest.json"))
        self.assertEqual(git.calls[1][-1], f"{BASE}..{HEAD}")
        self.assertNotIn("\x00", "".join(git.calls[1]))


class InputTest(unittest.TestCase):
    def test_rejects_bad_inputs(self):
        cases = {
            "dry-run": {"dry_run": "yes"},
            "manifest-file": {"manifest_file": "../x.json"},
            "package": {"package": "/abs"},
            "title": {"title": " "},
            "base-sha": {"base_sha": "main"},
            "head-sha": {"head_sha": ""},
        }
        for name, override in cases.items():
            with self.subTest(input=name), self.assertRaises(check.CheckError) as raised:
                check.check(args(**override), git=fake_git("2.0.0"))
            self.assertIn(name, str(raised.exception))

    def test_rejects_a_missing_package(self):
        with self.assertRaises(check.CheckError) as raised:
            check.check(args(package="packages/x"), git=fake_git("2.0.0"))
        self.assertIn("is not a package", str(raised.exception))

    def test_rejects_a_bad_manifest(self):
        for text in ("not json", "[]", '{".": 2}', '{".": "2.0"}'):
            with self.subTest(text=text), self.assertRaises(check.CheckError):
                check.check(args(), git=lambda *a: text)

    def test_main_reports_an_input_error(self):
        out = io.StringIO()
        with redirect_stdout(out):
            status = check.main(["--title", "fix: a", "--dry-run", "maybe"])
        self.assertEqual(status, 1)
        self.assertIn("::error::input dry-run", out.getvalue())


class DryRunTest(unittest.TestCase):
    def test_dry_run_reads_the_workspace_manifest_and_never_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "out")
            out = io.StringIO()
            git = fake_git("9.9.9")
            with redirect_stdout(out):
                status = check.check(args(workspace=str(FIXTURE), dry_run="true", base_sha="", head_sha="",
                                          title="feat(api)!: drop a thing", github_output=output), git=git)
            outputs = dict(line.split("=", 1) for line in Path(output).read_text().splitlines())
        self.assertEqual(status, 0)
        self.assertEqual(git.calls, [])
        expected = dict(line.split("=", 1) for line in (HERE / "fixtures" / "expected" / "basic.env").read_text().splitlines())
        self.assertEqual(outputs, expected)
        self.assertIn("::warning::pull request title", out.getvalue())

    def test_dry_run_without_the_manifest_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(check.CheckError):
            check.check(args(workspace=tmp, dry_run="true", base_sha="", head_sha=""), git=fake_git("1.0.0"))


if __name__ == "__main__":
    unittest.main()
