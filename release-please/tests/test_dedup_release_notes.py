"""Tests of dedup_release_notes.py.

The shas and subjects of the fixtures under fixtures/dedup come from real
release history (the 1.10.3 and 1.10.2 notes of a library, where
release-please listed a merge commit and its original commit with one
subject), with the repository name made generic. The exception is the
monorepo body fixture, which uses placeholder shas. The merge flags of those
shas are given by a fake lookup; the end-to-end cases build a real git
history in a temporary repository.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import dedup_release_notes as dedup  # noqa: E402  (path set above)

FIXTURES = os.path.join(HERE, "fixtures", "dedup")
# Merge commits of the real fixtures; every other sha is an ordinary commit.
REAL_MERGES = {"60628e2", "9a17d05", "1111111", "3333333"}


def fake_is_merge(sha):
    return sha[:7] in REAL_MERGES


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()


def entry(subject, sha):
    return f"* {subject} ([{sha}](https://github.com/o/r/commit/{sha * 6}))"


class DedupTextTest(unittest.TestCase):
    def test_real_changelog_drops_the_merge_entry_of_the_top_section_only(self):
        text = fixture("changelog-real.md")
        result, dropped = dedup.dedup_text(text, fake_is_merge, top_only=True)
        self.assertEqual(len(dropped), 1)
        self.assertIn("60628e2", dropped[0])
        self.assertNotIn("60628e2", result)
        self.assertIn("84b1047", result)
        # 1.10.2 is an older section: untouched in top-only mode.
        self.assertIn("9a17d05", result)

    def test_real_changelog_drops_every_section_when_not_top_only(self):
        result, dropped = dedup.dedup_text(fixture("changelog-real.md"), fake_is_merge, top_only=False)
        self.assertEqual(len(dropped), 2)
        self.assertNotIn("9a17d05", result)
        self.assertIn("96874a5", result)

    def test_pr_body_keeps_the_frame_and_the_original_commit(self):
        text = fixture("pr-body-real.md")
        result, dropped = dedup.dedup_text(text, fake_is_merge, top_only=False)
        self.assertEqual(len(dropped), 1)
        self.assertEqual(result.count("send the empty-state call-to-action"), 1)
        self.assertTrue(result.startswith(":robot: I have created a release *beep* *boop*\n---\n"))
        self.assertIn("\n---\nThis PR was generated with [Release Please]", result)
        self.assertIn("### Bug Fixes", result)

    def test_pr_body_of_a_monorepo_dedups_each_package_section(self):
        result, dropped = dedup.dedup_text(fixture("pr-body-monorepo.md"), fake_is_merge, top_only=False)
        self.assertEqual(len(dropped), 2)
        self.assertEqual(result.count("point the contact"), 1)
        self.assertEqual(result.count("explain the retry"), 1)
        self.assertIn("2222222", result)
        self.assertIn("4444444", result)
        self.assertEqual(result.count("<details>"), 2)
        self.assertEqual(result.count("</details>"), 2)

    def test_two_ordinary_commits_with_one_subject_are_both_kept(self):
        text = "## 1.0.0\n\n### Features\n\n" + entry("add x", "aaaaaaa") + "\n" + entry("add x", "bbbbbbb") + "\n"
        result, dropped = dedup.dedup_text(text, lambda sha: False, top_only=True)
        self.assertEqual(dropped, [])
        self.assertEqual(result, text)

    def test_merge_entries_alone_are_kept_when_no_ordinary_commit_repeats_them(self):
        text = "## 1.0.0\n\n### Features\n\n" + entry("add x", "aaaaaaa") + "\n" + entry("add x", "bbbbbbb") + "\n"
        result, dropped = dedup.dedup_text(text, lambda sha: True, top_only=True)
        self.assertEqual(dropped, [])
        self.assertEqual(result, text)

    def test_a_merge_entry_with_a_unique_subject_is_kept_and_never_looked_up(self):
        looked_up = []

        def is_merge(sha):
            looked_up.append(sha)
            return True

        text = "## 1.0.0\n\n### Features\n\n" + entry("add x", "aaaaaaa") + "\n"
        result, dropped = dedup.dedup_text(text, is_merge, top_only=True)
        self.assertEqual((result, dropped, looked_up), (text, [], []))

    def test_the_same_subject_in_two_versions_is_not_a_duplicate(self):
        text = (
            "## 2.0.0\n\n### Bug Fixes\n\n" + entry("fix x", "aaaaaaa") + "\n\n"
            "## 1.0.0\n\n### Bug Fixes\n\n" + entry("fix x", "bbbbbbb") + "\n"
        )
        result, dropped = dedup.dedup_text(text, lambda sha: sha == "aaaaaaa", top_only=False)
        self.assertEqual((result, dropped), (text, []))

    def test_a_group_left_empty_loses_its_heading(self):
        text = (
            "## 1.0.0\n\n\n### Bug Fixes\n\n" + entry("fix x", "aaaaaaa") + "\n\n\n"
            "### Documentation\n\n" + entry("fix x", "bbbbbbb") + "\n"
        )
        result, dropped = dedup.dedup_text(text, lambda sha: sha == "aaaaaaa", top_only=True)
        self.assertEqual(len(dropped), 1)
        self.assertNotIn("### Bug Fixes", result)
        self.assertIn("### Documentation", result)

    def test_the_full_sha_of_the_url_is_what_gets_looked_up(self):
        looked_up = []

        def is_merge(sha):
            looked_up.append(sha)
            return sha == "a" * 40

        text = (
            "## 1.0.0\n\n* fix x ([aaaaaaa](https://github.com/o/r/commit/" + "a" * 40 + "))\n"
            "* fix x ([bbbbbbb](https://github.com/o/r/commit/" + "b" * 40 + "))\n"
        )
        _, dropped = dedup.dedup_text(text, is_merge, top_only=True)
        self.assertEqual(looked_up, ["a" * 40, "b" * 40])
        self.assertEqual(len(dropped), 1)

    def test_the_link_text_is_the_fallback_when_the_url_has_no_full_sha(self):
        looked_up = []
        text = "## 1.0.0\n\n" + entry("fix x", "aaaaaaa") + "\n" + entry("fix x", "bbbbbbb") + "\n"
        dedup.dedup_text(text, lambda sha: looked_up.append(sha) or False, top_only=True)
        self.assertEqual(looked_up, ["aaaaaaa", "bbbbbbb"])

    def test_a_merge_entry_is_kept_when_the_ordinary_commit_was_not_brought_in_by_it(self):
        text = "## 1.0.0\n\n" + entry("fix x", "aaaaaaa") + "\n" + entry("fix x", "bbbbbbb") + "\n"
        merge_only = lambda sha: sha == "aaaaaaa"  # noqa: E731
        result, dropped = dedup.dedup_text(text, merge_only, True, lambda sha, merge: False)
        self.assertEqual((result, dropped), (text, []))
        _, dropped = dedup.dedup_text(text, merge_only, True, lambda sha, merge: (sha, merge) == ("bbbbbbb", "aaaaaaa"))
        self.assertEqual(len(dropped), 1)

    def test_the_second_run_is_a_no_op(self):
        first, _ = dedup.dedup_text(fixture("pr-body-monorepo.md"), fake_is_merge, top_only=False)
        second, dropped = dedup.dedup_text(first, fake_is_merge, top_only=False)
        self.assertEqual((second, dropped), (first, []))

    def test_text_without_a_version_section_is_untouched(self):
        text = "# Changelog\n\nnothing yet\n"
        self.assertEqual(dedup.dedup_text(text, fake_is_merge, top_only=True), (text, []))


def git(repo, *args):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.org",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.org",
    }
    result = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True, env=env)
    return result.stdout.strip()


def run_main(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = dedup.main(list(argv))
    return code, out.getvalue()


class GitHistoryTest(unittest.TestCase):
    """A real history: one GitHub-style merge commit and two plain commits."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name
        git(self.repo, "init", "--quiet", "-b", "main")
        self.write("README.md", "x\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "--quiet", "-m", "chore: init")
        git(self.repo, "checkout", "--quiet", "-b", "feature")
        self.write("a.txt", "a\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "--quiet", "-m", "fix(block): send the call-to-action as an object")
        self.original = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "checkout", "--quiet", "main")
        # The default merge message of GitHub: "Merge pull request ..." then the subject.
        git(
            self.repo, "merge", "--no-ff", "--quiet", "feature",
            "-m", "Merge pull request #9 from o/feature",
            "-m", "fix(block): send the call-to-action as an object",
        )
        self.merge = git(self.repo, "rev-parse", "HEAD")
        for name in ("one", "two"):
            self.write(f"{name}.txt", name)
            git(self.repo, "add", ".")
            git(self.repo, "commit", "--quiet", "-m", "feat: add the thing")
        self.plain = git(self.repo, "rev-parse", "HEAD"), git(self.repo, "rev-parse", "HEAD~1")
        self.changelog = os.path.join(self.repo, "CHANGELOG.md")
        lines = [
            "# Changelog", "", "## [1.1.0](https://github.com/o/r/compare/v1.0.0...v1.1.0) (2026-11-20)", "", "",
            "### Features", "", entry("add the thing", self.plain[0][:7]), entry("add the thing", self.plain[1][:7]),
            "", "", "### Bug Fixes", "",
            f"* **block:** send the call-to-action as an object ([{self.merge[:7]}](https://github.com/o/r/commit/{self.merge}))",
            f"* **block:** send the call-to-action as an object ([{self.original[:7]}](https://github.com/o/r/commit/{self.original}))",
            "",
        ]
        self.write("CHANGELOG.md", "\n".join(lines))

    def write(self, name, content):
        with open(os.path.join(self.repo, name), "w", encoding="utf-8") as handle:
            handle.write(content)

    def read_changelog(self):
        with open(self.changelog, encoding="utf-8") as handle:
            return handle.read()

    def test_git_is_merge_tells_a_merge_commit_from_an_ordinary_one(self):
        is_merge = dedup.git_is_merge(self.repo)
        self.assertTrue(is_merge(self.merge[:7]))
        self.assertFalse(is_merge(self.original[:7]))
        self.assertFalse(is_merge(self.plain[0]))

    def test_a_full_sha_url_wins_over_an_ambiguous_or_unknown_link_text(self):
        # The link text is an abbreviation git cannot resolve; the URL carries the full sha.
        lines = [
            "## 1.1.0",
            "",
            f"* **block:** send the call-to-action as an object ([0000000](https://github.com/o/r/commit/{self.merge}))",
            f"* **block:** send the call-to-action as an object ([0000001](https://github.com/o/r/commit/{self.original}))",
            "",
        ]
        self.write("CHANGELOG.md", "\n".join(lines))
        code, _ = run_main("--repo-dir", self.repo, "--changelog", "CHANGELOG.md")
        self.assertEqual(code, 0)
        after = self.read_changelog()
        self.assertNotIn("0000000", after)
        self.assertIn("0000001", after)

    def test_a_merge_entry_survives_when_the_same_subject_commit_is_not_on_its_branch(self):
        subject = "fix(block): send the call-to-action as an object"
        git(self.repo, "checkout", "--quiet", "-b", "other", "main~2")
        self.write("o.txt", "o\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "--quiet", "-m", "chore: unrelated work")
        git(
            self.repo, "checkout", "--quiet", "main",
        )
        git(self.repo, "merge", "--no-ff", "--quiet", "other", "-m", "Merge pull request #10 from o/other", "-m", subject)
        stray = git(self.repo, "rev-parse", "HEAD")
        self.write("same.txt", "s\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "--quiet", "-m", subject)
        ordinary = git(self.repo, "rev-parse", "HEAD")
        lines = [
            "## 1.2.0", "",
            f"* {subject[len('fix(block): '):]} ([{stray[:7]}](https://github.com/o/r/commit/{stray}))",
            f"* {subject[len('fix(block): '):]} ([{ordinary[:7]}](https://github.com/o/r/commit/{ordinary}))",
            "",
        ]
        self.write("CHANGELOG.md", "\n".join(lines))
        before = self.read_changelog()
        code, _ = run_main("--repo-dir", self.repo, "--changelog", "CHANGELOG.md")
        self.assertEqual(code, 0)
        self.assertEqual(self.read_changelog(), before)

    def test_unknown_sha_fails_with_a_clear_message(self):
        with self.assertRaises(dedup.DedupError) as caught:
            dedup.git_is_merge(self.repo)("deadbeef")
        self.assertIn("full history", str(caught.exception))

    def test_check_reports_without_writing_then_write_then_check_passes(self):
        before = self.read_changelog()
        code, output = run_main("--repo-dir", self.repo, "--changelog", "CHANGELOG.md", "--check")
        self.assertEqual(code, 1)
        self.assertIn("::error file=", output)
        self.assertEqual(self.read_changelog(), before)

        code, _ = run_main("--repo-dir", self.repo, "--changelog", "CHANGELOG.md")
        self.assertEqual(code, 0)
        after = self.read_changelog()
        self.assertNotIn(self.merge[:7], after)
        self.assertIn(self.original[:7], after)
        # Two ordinary commits with one subject stay.
        self.assertEqual(after.count("add the thing"), 2)

        code, _ = run_main("--repo-dir", self.repo, "--changelog", "CHANGELOG.md", "--check")
        self.assertEqual(code, 0)

    def test_second_run_leaves_the_file_byte_identical(self):
        run_main("--repo-dir", self.repo, "--changelog", "CHANGELOG.md")
        first = self.read_changelog()
        mtime = os.stat(self.changelog).st_mtime_ns
        code, output = run_main("--repo-dir", self.repo, "--changelog", "CHANGELOG.md")
        self.assertEqual(code, 0)
        self.assertIn("dropped 0", output)
        self.assertEqual(self.read_changelog(), first)
        self.assertEqual(os.stat(self.changelog).st_mtime_ns, mtime)

    def test_a_lookup_failure_writes_nothing(self):
        before = self.read_changelog()
        body = os.path.join(self.repo, "body.md")
        with open(body, "w", encoding="utf-8") as handle:
            handle.write("## 1.0.0\n\n" + entry("x", "deadbee") + "\n" + entry("x", "cafebab") + "\n")
        code, output = run_main("--repo-dir", self.repo, "--changelog", "CHANGELOG.md", "--body-file", body)
        self.assertEqual(code, 1)
        self.assertIn("not found", output)
        self.assertEqual(self.read_changelog(), before)

    def test_config_file_selects_the_changelogs_changed_since_the_base(self):
        git(self.repo, "branch", "base", "HEAD")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "--quiet", "-m", "chore: release")
        config = {"packages": {".": {}, "sub": {"changelog-path": "NOTES.md"}}}
        self.write("release-please-config.json", json.dumps(config))
        paths = dedup.changelogs_from_config(self.repo, "release-please-config.json", "")
        self.assertEqual(paths, ["CHANGELOG.md", "sub/NOTES.md"])
        paths = dedup.changelogs_from_config(self.repo, "release-please-config.json", "base")
        self.assertEqual(paths, ["CHANGELOG.md"])

    def test_a_top_level_changelog_path_is_the_default_of_every_package(self):
        config = {"changelog-path": "NOTES.md", "packages": {".": {}, "sub": {}, "own": {"changelog-path": "OWN.md"}}}
        self.write("release-please-config.json", json.dumps(config))
        paths = dedup.changelogs_from_config(self.repo, "release-please-config.json", "")
        self.assertEqual(paths, ["NOTES.md", "sub/NOTES.md", "own/OWN.md"])

    def test_a_changelog_path_that_leaves_the_repository_is_refused(self):
        code, output = run_main("--repo-dir", self.repo, "--changelog", "../CHANGELOG.md")
        self.assertEqual(code, 1)
        self.assertIn("relative path", output)


if __name__ == "__main__":
    unittest.main()
