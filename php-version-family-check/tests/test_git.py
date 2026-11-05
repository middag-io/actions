"""End-to-end tests of scripts/check.py against a real git repository.

Builds a temporary repository: main with the manifest, a feature branch kept
current by a merge of main, and runs the script as the action does
(python3 -I check.py), so the git calls, the record separators and the
skipping of merge commits are exercised for real.

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


class GitRepositoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo = cls.tmp.name
        cls.git("init", "-q", "-b", "main")
        cls.write(MANIFEST, '{".": "2.0.3"}\n')
        cls.git("add", MANIFEST)
        cls.git("commit", "-q", "-m", "chore: release 2.0.3")
        cls.git("switch", "-q", "-c", "topic")
        cls.write("a.txt", "a\n")
        cls.git("add", "a.txt")
        cls.git("commit", "-q", "-m", "fix: correct a typo")
        cls.git("switch", "-q", "main")
        cls.write("b.txt", "b\n")
        cls.git("add", "b.txt")
        # On main already: never a commit of the pull request.
        cls.git("commit", "-q", "-m", "feat!: landed on main earlier\n\nRelease-As: 9.0.0")
        cls.base = cls.git("rev-parse", "HEAD").strip()
        cls.git("switch", "-q", "topic")
        cls.git("merge", "-q", "--no-ff", "-m", "feat!: a merge commit is skipped", "main")
        cls.write("c.txt", "c\n")
        cls.git("add", "c.txt")
        cls.git("commit", "-q", "-m", "feat(api): add a thing\n\nBody line.")
        cls.head = cls.git("rev-parse", "HEAD").strip()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def git(cls, *args):
        env = {**os.environ, **GIT_ENV}
        return subprocess.run(["git", "-C", cls.repo, *args], check=True, capture_output=True, text=True, env=env).stdout

    @classmethod
    def write(cls, name, text):
        Path(cls.repo, name).write_text(text, encoding="utf-8")

    def run_check(self, window, base_ref="main"):
        output = os.path.join(self.repo, ".out")
        Path(output).write_text("", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), f"--window={window}", "--family-from=2.0.0",
             "--title=fix: a neutral title", f"--base-sha={self.base}", f"--head-sha={self.head}",
             f"--base-ref={base_ref}", "--window-branch=main", f"--workspace={self.repo}",
             f"--github-output={output}"],
            capture_output=True, text=True, check=False, env={**os.environ, **GIT_ENV},
        )
        outputs = dict(line.split("=", 1) for line in Path(output).read_text(encoding="utf-8").splitlines())
        return result.returncode, result.stdout, outputs

    def test_closed_window_fails_only_on_the_commits_of_the_pull_request(self):
        status, out, outputs = self.run_check("")
        self.assertEqual((status, outputs["version"], outputs["findings"]), (1, "2.0.3", "1"))
        self.assertIn("3 message(s) checked", out)  # title, fix, feat; not the merge, not main's commit
        self.assertIn("(feat(api): add a thing): type feat needs an open family window", out)
        self.assertNotIn("landed on main earlier", out)
        self.assertNotIn("a merge commit is skipped", out)

    def test_minor_window_passes(self):
        status, _, outputs = self.run_check("2.1")
        self.assertEqual((status, outputs["result"], outputs["window-kind"]), (0, "pass", "minor"))

    def test_minor_window_is_closed_for_another_base_branch(self):
        status, _, outputs = self.run_check("2.1", base_ref="release/2.0")
        self.assertEqual((status, outputs["window-kind"]), (1, "closed"))

    def test_unknown_base_sha_is_an_error(self):
        self.base, saved = "f" * 40, self.base
        try:
            status, out, _ = self.run_check("")
        finally:
            self.base = saved
        self.assertEqual(status, 1)
        self.assertIn("::error::git show", out)


if __name__ == "__main__":
    unittest.main()
