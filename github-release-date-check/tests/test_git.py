"""End-to-end tests of scripts/check.py against a real git repository.

Builds a temporary repository with explicit committer dates: a branch whose
commits were made before the release of main and one recreated after it
with cherry-pick, the command the guidance gives. The script runs as the
action runs it (python3 -I check.py), so the git calls, the record
separators, the skipping of merge commits and the tag lookup are real.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "check.py"
MANIFEST = ".release-please-manifest.json"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
}
DAY_1, DAY_2, DAY_3, DAY_4 = (f"2030-01-0{n}T12:00:00+00:00" for n in (1, 2, 3, 4))


class GitRepositoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo = cls.tmp.name
        cls.git("init", "-q", "-b", "main")
        cls.commit(DAY_1, "chore: start", MANIFEST, '{".": "1.0.0", "b.txt": "1.1.0"}\n')
        cls.git("switch", "-q", "-c", "old-topic")
        cls.commit(DAY_2, "feat: made before the release", "a.txt", "a\n")
        cls.git("switch", "-q", "main")
        cls.commit(DAY_3, "chore: release 1.1.0", MANIFEST, '{".": "1.1.0", "b.txt": "1.1.0"}\n')
        cls.git("tag", "v1.1.0")
        cls.git("update-ref", "refs/remotes/origin/main", "HEAD")
        # Kept current by a merge of main: the merge commit and main's commits are skipped.
        cls.git("switch", "-q", "old-topic")
        cls.git("merge", "-q", "--no-ff", "-m", "Merge branch 'main' into old-topic", "main", env={"GIT_COMMITTER_DATE": DAY_4})
        cls.commit(DAY_4, "fix: made after the release", "b.txt", "b\n")
        cls.old_head = cls.git("rev-parse", "HEAD").strip()
        # The guidance: a new branch from the base, the commits cherry-picked (a new committer date).
        cls.git("switch", "-q", "-c", "new-topic", "origin/main")
        picks = cls.git("rev-list", "--reverse", "--no-merges", f"origin/main..{cls.old_head}")
        cls.git("cherry-pick", "--stdin", input=picks, env={"GIT_COMMITTER_DATE": DAY_4})
        cls.new_head = cls.git("rev-parse", "HEAD").strip()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def git(cls, *args, env=None, input=None):
        full_env = {**os.environ, **GIT_ENV, **(env or {})}
        return subprocess.run(["git", "-C", cls.repo, *args], check=True, capture_output=True, text=True,
                              env=full_env, input=input).stdout

    @classmethod
    def commit(cls, date, message, name, text):
        Path(cls.repo, name).write_text(text, encoding="utf-8")
        cls.git("add", name)
        cls.git("commit", "-q", "-m", message, env={"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date})

    def run_check(self, head, tag_format="v{version}", package="."):
        output = os.path.join(self.repo, ".git", "out")
        Path(output).write_text("", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--base-ref=main", f"--head-sha={head}", f"--tag-format={tag_format}",
             f"--package={package}", f"--workspace={self.repo}", f"--github-output={output}"],
            capture_output=True, text=True, check=False, env={**os.environ, **GIT_ENV},
        )
        outputs = dict(line.split("=", 1) for line in Path(output).read_text(encoding="utf-8").splitlines())
        return result.returncode, result.stdout, outputs

    def test_a_commit_made_before_the_release_fails(self):
        status, out, outputs = self.run_check(self.old_head)
        self.assertEqual((status, outputs["result"], outputs["findings"], outputs["tag"]), (1, "fail", "1", "v1.1.0"))
        self.assertIn("2 commit(s)", out)  # the two commits of the branch; not the merge, not main's commits
        self.assertIn("(feat: made before the release) has the committer date 2030-01-02", out)

    def test_the_branch_recreated_with_cherry_pick_passes(self):
        status, out, outputs = self.run_check(self.new_head)
        self.assertEqual((status, outputs["result"], outputs["findings"]), (0, "pass", "0"))
        # cherry-pick keeps the author date: only the committer date counts.
        self.assertIn(DAY_2.replace("+00:00", " +0000").replace("T", " "),
                      self.git("log", "-1", "--format=%ai", f"{self.new_head}~1"))
        self.assertIn("2 commit(s)", out)

    def test_a_package_ignores_the_commits_outside_its_folder(self):
        status, _, outputs = self.run_check(self.old_head, package="b.txt")
        self.assertEqual((status, outputs["result"], outputs["findings"]), (0, "pass", "0"))

    def test_a_shallow_checkout_is_an_error(self):
        with tempfile.TemporaryDirectory() as shallow:
            subprocess.run(["git", "clone", "-q", "--depth=1", f"file://{self.repo}", shallow], check=True,
                           capture_output=True, env={**os.environ, **GIT_ENV})
            result = subprocess.run(
                [sys.executable, "-I", str(SCRIPT), "--base-ref=main", f"--head-sha={self.old_head}",
                 f"--workspace={shallow}"], capture_output=True, text=True, check=False, env={**os.environ, **GIT_ENV})
            self.assertEqual(result.returncode, 1)
            self.assertIn("the checkout is shallow", result.stdout)

    def test_a_missing_tag_warns_and_passes(self):
        status, out, outputs = self.run_check(self.old_head, tag_format="release-{version}")
        self.assertEqual((status, outputs["result"]), (0, "skip"))
        self.assertIn("::warning::release tag release-1.1.0", out)


if __name__ == "__main__":
    unittest.main()
