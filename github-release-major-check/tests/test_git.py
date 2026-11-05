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
        cls.write(MANIFEST, '{".": "2.4.1"}\n')
        cls.git("add", MANIFEST)
        cls.git("commit", "-q", "-m", "chore: release 2.4.1")
        cls.git("switch", "-q", "-c", "topic")
        cls.write("a.txt", "a\n")
        cls.git("add", "a.txt")
        cls.git("commit", "-q", "-m", "refactor(api)!: drop the old endpoint")
        cls.git("switch", "-q", "main")
        cls.write("b.txt", "b\n")
        cls.git("add", "b.txt")
        # On main already: never a commit of the pull request.
        cls.git("commit", "-q", "-m", "chore: landed on main earlier\n\nRelease-As: 3.0.0")
        cls.base = cls.git("rev-parse", "HEAD").strip()
        cls.git("switch", "-q", "topic")
        cls.git("merge", "-q", "--no-ff", "-m", "Merge branch 'main' into topic\n\nRelease-As: 3.0.0", "main")
        cls.write("c.txt", "c\n")
        cls.git("add", "c.txt")
        cls.git("commit", "-q", "-m", "fix: a compatible fix")
        cls.without = cls.git("rev-parse", "HEAD").strip()
        cls.git("commit", "-q", "--allow-empty", "-m", "chore: release the next major\n\nRelease-As: 3.0.0")
        cls.explicit = cls.git("rev-parse", "HEAD").strip()

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

    def run_check(self, head, base=None):
        output = os.path.join(self.repo, ".out")
        Path(output).write_text("", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--title=fix: a neutral title", f"--base-sha={base or self.base}",
             f"--head-sha={head}", f"--workspace={self.repo}", f"--github-output={output}"],
            capture_output=True, text=True, check=False, env={**os.environ, **GIT_ENV},
        )
        outputs = dict(line.split("=", 1) for line in Path(output).read_text(encoding="utf-8").splitlines())
        return result.returncode, result.stdout, outputs

    def test_breaking_commit_without_its_own_release_as_fails(self):
        status, out, outputs = self.run_check(self.without)
        self.assertEqual((status, outputs["version"], outputs["findings"]), (1, "2.4.1", "1"))
        self.assertIn("3 message(s) checked", out)  # title, refactor!, fix; not the merge, not main's commit
        self.assertIn("(refactor(api)!: drop the old endpoint): a breaking change", out)

    def test_explicit_release_as_in_the_pull_request_passes(self):
        status, _, outputs = self.run_check(self.explicit)
        self.assertEqual((status, outputs["result"], outputs["release-as-major"]), (0, "pass", "3.0.0"))

    def test_unknown_base_sha_is_an_error(self):
        status, out, _ = self.run_check(self.without, base="f" * 40)
        self.assertEqual(status, 1)
        self.assertIn("::error::git show", out)


if __name__ == "__main__":
    unittest.main()
