"""Tests of scripts/check.py: the window, the version gate, the messages and the run.

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


def fake_git(version, commits=()):
    """Returns a git stand-in: show gives the manifest, log gives the commits."""
    calls = []

    def git(*args):
        calls.append(args)
        if args[0] == "show":
            return '{".": "%s"}' % version
        if args[0] == "log":
            return "".join(commits)
        raise AssertionError(f"unexpected git call {args}")

    git.calls = calls
    return git


def args(**overrides):
    values = {
        "window": "", "manifest_file": ".release-please-manifest.json", "package": ".",
        "family_from": "2.0.0", "title": "fix: correct a typo", "base_sha": BASE, "head_sha": HEAD,
        "dry_run": "false", "workspace": ".", "github_output": "",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def run(version, commits=(), **overrides):
    """Returns (status, stdout, outputs) of one check."""
    with tempfile.TemporaryDirectory() as tmp:
        output = os.path.join(tmp, "out")
        out = io.StringIO()
        with redirect_stdout(out):
            status = check.check(args(github_output=output, **overrides), git=fake_git(version, commits))
        outputs = dict(line.split("=", 1) for line in Path(output).read_text().splitlines())
    return status, out.getvalue(), outputs


class WindowTest(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(check.parse_window("")[0], check.CLOSED)
        self.assertEqual(check.parse_window("  \n")[0], check.CLOSED)
        self.assertEqual(check.parse_window("2.1")[0], check.MINOR)
        self.assertEqual(check.parse_window("2.0")[0], check.MAJOR)
        self.assertEqual(check.parse_window("3.0\n")[0], check.MAJOR)

    def test_rejects_bad_values(self):
        for value in ("2", "2.1.0", "v2.1", "2.x", "02.1", "open"):
            with self.subTest(value=value), self.assertRaises(check.CheckError):
                check.parse_window(value)


class VersionTest(unittest.TestCase):
    def at_least(self, version, start):
        return check.version_at_least(check.parse_version("v", version), check.parse_version("s", start))

    def test_precedence(self):
        self.assertTrue(self.at_least("2.0.0", "2.0.0"))
        self.assertTrue(self.at_least("2.3.1", "2.0.0"))
        self.assertFalse(self.at_least("1.31.3", "2.0.0"))
        self.assertFalse(self.at_least("0.9.0", "2.0.0"))
        self.assertFalse(self.at_least("2.0.0-rc.1", "2.0.0"))
        self.assertTrue(self.at_least("2.0.0", "2.0.0-rc.1"))
        self.assertTrue(self.at_least("2.0.0-rc.2", "2.0.0-rc.1"))
        self.assertFalse(self.at_least("2.0.0-rc.1", "2.0.0-rc.2"))

    def test_rejects_bad_versions(self):
        for value in ("2", "2.0", "v2.0.0", "2.0.0.0"):
            with self.subTest(value=value), self.assertRaises(check.CheckError):
                check.parse_version("version", value)


class MessageTest(unittest.TestCase):
    def test_headers(self):
        cases = {
            "feat: add a thing": ("feat", False),
            "feat(api): add a thing": ("feat", False),
            "feat(api)!: drop a thing": ("feat", True),
            "fix!: change a default": ("fix", True),
            "Feat: add a thing": ("feat", False),
            "revert: feat: add a thing": ("revert", False),
            "chore: update a pin": ("chore", False),
            'Revert "feat: add a thing"': (None, False),
            "feature: not a type we gate": ("feature", False),
            "feat:no space": (None, False),
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(check.parse_message(message), expected)

    def test_breaking_footers(self):
        self.assertEqual(check.parse_message("fix: a\n\nBREAKING CHANGE: the input x is gone"), ("fix", True))
        self.assertEqual(check.parse_message("fix: a\n\nBREAKING-CHANGE: the input x is gone"), ("fix", True))
        self.assertEqual(check.parse_message("fix: a\n\nThis is not a BREAKING CHANGE: prose"), ("fix", False))
        self.assertEqual(check.parse_message("BREAKING CHANGE: only a header"), (None, False))


class CheckTest(unittest.TestCase):
    def test_closed_window_feat_on_1x_passes(self):
        status, _, outputs = run("1.31.3", title="feat: add a thing", commits=[commit("1" * 40, "feat: add a thing")])
        self.assertEqual((status, outputs["result"], outputs["window-kind"]), (0, "pass", "closed"))

    def test_closed_window_feat_on_0x_passes(self):
        status, _, _ = run("0.4.0", title="feat: add a thing")
        self.assertEqual(status, 0)

    def test_closed_window_feat_on_2x_fails(self):
        status, out, outputs = run("2.0.3", title="feat: add a thing")
        self.assertEqual((status, outputs["result"], outputs["findings"]), (1, "fail", "1"))
        self.assertIn("::error::pull request title: type feat needs an open family window", out)
        self.assertIn("merging the base branch into it", out)
        self.assertIn("re-run this check", out)

    def test_feat_commit_fails_even_with_a_neutral_title(self):
        status, out, _ = run("2.1.0", commits=[commit("1234567" + "0" * 33, "feat(api): add a thing\n\nbody")])
        self.assertEqual(status, 1)
        self.assertIn("commit 1234567 (feat(api): add a thing): type feat", out)

    def test_minor_window_feat_on_2x_passes(self):
        status, _, outputs = run("2.0.3", window="2.1", title="feat(api): add a thing",
                                 commits=[commit("1" * 40, "feat(api): add a thing")])
        self.assertEqual((status, outputs["window-kind"]), (0, "minor"))

    def test_bang_fails_outside_a_major_window(self):
        for window in ("", "2.1"):
            for version in ("1.7.0", "2.0.0"):
                with self.subTest(window=window, version=version):
                    status, out, _ = run(version, window=window, title="feat(api)!: drop a thing")
                    self.assertEqual(status, 1)
                    self.assertIn("needs a family MAJOR window", out)

    def test_breaking_footer_fails_outside_a_major_window(self):
        status, out, _ = run("2.0.0", window="2.1",
                             commits=[commit("1" * 40, "fix: rename x\n\nBREAKING CHANGE: x is now y")])
        self.assertEqual(status, 1)
        self.assertIn("needs a family MAJOR window", out)

    def test_major_window_allows_bang_on_1x(self):
        status, _, outputs = run("1.31.3", window="2.0", title="feat(core)!: drop the old API",
                                 commits=[commit("1" * 40, "refactor!: drop x\n\nBREAKING CHANGE: x is gone")])
        self.assertEqual((status, outputs["window-kind"]), (0, "major"))

    def test_major_window_allows_feat_on_2x(self):
        status, _, _ = run("2.4.1", window="3.0", title="feat: add a thing")
        self.assertEqual(status, 0)

    def test_merge_commits_are_skipped(self):
        merge = commit("1" * 40, "Merge branch 'main' into feat/x\n\nfeat!: from main", parents=2)
        status, out, _ = run("2.0.0", commits=[merge, commit("2" * 40, "fix: a")])
        self.assertEqual(status, 0)
        self.assertIn("2 message(s) checked", out)

    def test_revert_and_chore_pass(self):
        commits = [commit("1" * 40, "revert: feat: add a thing"), commit("2" * 40, "chore: update a pin"),
                   commit("3" * 40, 'Revert "feat: add a thing"')]
        status, _, _ = run("2.0.0", title="revert: feat: add a thing", commits=commits)
        self.assertEqual(status, 0)

    def test_empty_family_from_gates_every_version(self):
        status, _, _ = run("1.0.0", family_from="", title="feat: add a thing")
        self.assertEqual(status, 1)

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
            "window": {"window": "next"},
            "dry-run": {"dry_run": "yes"},
            "manifest-file": {"manifest_file": "../x.json"},
            "package": {"package": "/abs"},
            "family-from": {"family_from": "2.0"},
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

    def test_main_reports_an_input_error(self):
        out = io.StringIO()
        with redirect_stdout(out):
            status = check.main(["--window", "next", "--title", "fix: a", "--dry-run", "true"])
        self.assertEqual(status, 1)
        self.assertIn("::error::input window", out.getvalue())


class DryRunTest(unittest.TestCase):
    def test_dry_run_reads_the_workspace_manifest_and_never_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "out")
            out = io.StringIO()
            git = fake_git("9.9.9")
            with redirect_stdout(out):
                status = check.check(args(workspace=str(FIXTURE), dry_run="true", base_sha="", head_sha="",
                                          title="feat!: drop a thing", github_output=output), git=git)
            outputs = dict(line.split("=", 1) for line in Path(output).read_text().splitlines())
        self.assertEqual(status, 0)
        self.assertEqual(git.calls, [])
        self.assertEqual((outputs["version"], outputs["result"]), ("2.0.0", "fail"))
        self.assertIn("::warning::pull request title", out.getvalue())


if __name__ == "__main__":
    unittest.main()
