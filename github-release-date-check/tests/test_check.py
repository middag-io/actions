"""Unit tests of scripts/check.py: inputs, manifest, tag, evaluation.

git is replaced by a fake that answers from a table, so these tests cover
the decisions; test_git.py runs the script against a real repository.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("check", HERE.parent / "scripts" / "check.py")
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)

FIXTURES = HERE / "fixtures"
BASE = "refs/remotes/origin/main"
HEAD = "b" * 40
RELEASE = "a" * 40


def args(**overrides):
    values = {
        "manifest_file": ".release-please-manifest.json", "package": ".", "tag_format": "v{version}",
        "base_ref": "main", "head_sha": HEAD, "dry_run": "false", "workspace": ".", "github_output": "",
    }
    values.update(overrides)
    return check.build_parser().parse_args([f"--{k.replace('_', '-')}={v}" for k, v in values.items()])


def fake_git(tags=None, manifest='{".": "1.4.2"}', commits=(), release_time=1000, shallow=False, calls=None):
    tags = {"v1.4.2": RELEASE} if tags is None else tags
    refs = {BASE: "c" * 40, **{f"refs/tags/{name}": sha for name, sha in tags.items()}}

    def git(*argv):
        if calls is not None:
            calls.append(argv)
        if argv[:2] == ("rev-parse", "--is-shallow-repository"):
            return "true\n" if shallow else "false\n"
        if argv[0] == "for-each-ref":
            return f"{argv[-1]}\n" if argv[-1] in refs else ""
        if argv[0] == "rev-parse":
            return refs[argv[-1].removesuffix("^{commit}")] + "\n"
        if argv[0] == "show":
            return manifest
        if argv[:2] == ("log", "-1"):
            return "1970-01-01T00:16:40+00:00\n" if argv[2] == "--format=%cI" else f"{release_time}\n"
        if argv[:3] == ("log", "-z", "--no-merges"):
            return "".join(f"{s} {t} date-{t} {h}\x00" for s, t, h in commits)
        raise AssertionError(argv)
    return git


def run(arguments, git):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            status = check.check(arguments, git)
        except check.CheckError as error:
            check.annotate("error", str(error))
            status = 1
    return status, out.getvalue()


class InputTest(unittest.TestCase):
    def test_rejects_a_tag_format_without_exactly_one_version(self):
        for value in ("v", "v{version}{version}", "v {version}", "$(x){version}"):
            with self.subTest(value=value):
                status, out = run(args(tag_format=value), fake_git())
                self.assertEqual(status, 1)
                self.assertIn("input tag-format", out)

    def test_rejects_an_unsafe_base_ref(self):
        for value in ("", "-x", "a..b", "main/", "a b", "a@{1}", "x.lock"):
            with self.subTest(value=value):
                status, out = run(args(base_ref=value), fake_git())
                self.assertEqual(status, 1)
                self.assertIn("input base-ref", out)

    def test_rejects_a_short_head_sha(self):
        status, out = run(args(head_sha="abc"), fake_git())
        self.assertEqual(status, 1)
        self.assertIn("input head-sha", out)

    def test_rejects_an_unknown_package(self):
        status, out = run(args(package="packages/x"), fake_git())
        self.assertEqual(status, 1)
        self.assertIn("is not a package", out)


class EvaluationTest(unittest.TestCase):
    def test_commits_after_the_release_pass(self):
        status, out = run(args(), fake_git(commits=[("1" * 40, 1001, "feat: x"), ("2" * 40, 2000, "fix: y")]))
        self.assertEqual(status, 0)
        self.assertIn("2 commit(s)", out)
        self.assertIn("0 finding(s)", out)

    def test_a_commit_dated_before_or_at_the_release_fails_with_the_guidance(self):
        commits = [("1" * 40, 999, "feat: old"), ("2" * 40, 1000, "fix: same second"), ("3" * 40, 1001, "fix: new")]
        status, out = run(args(), fake_git(commits=commits))
        self.assertEqual(status, 1)
        self.assertIn("2 finding(s)", out)
        self.assertIn("::error::commit 1111111 (feat: old)", out)
        self.assertIn("::error::commit 2222222 (fix: same second)", out)
        self.assertNotIn("3333333", out)
        self.assertIn("cherry-pick --stdin", out)
        self.assertIn(f"origin/main..{HEAD}", out)
        self.assertIn("Do not rebase", out)

    def test_a_missing_tag_warns_and_passes(self):
        status, out = run(args(), fake_git(tags={}, commits=[("1" * 40, 1, "feat: old")]))
        self.assertEqual(status, 0)
        self.assertIn("::warning::release tag v1.4.2", out)

    def test_tag_format_without_v_and_with_a_component(self):
        for tag_format, tag in (("{version}", "1.4.2"), ("api-v{version}", "api-v1.4.2")):
            with self.subTest(tag_format=tag_format):
                status, out = run(args(tag_format=tag_format), fake_git(tags={tag: RELEASE}, commits=[("1" * 40, 5, "feat: old")]))
                self.assertEqual(status, 1)
                self.assertIn(f"release {tag}:", out)

    def test_a_subject_with_spaces_and_control_bytes_is_kept(self):
        status, out = run(args(), fake_git(commits=[("1" * 40, 5, "feat: a \x1e b  c")]))
        self.assertEqual(status, 1)
        self.assertIn("(feat: a \x1e b  c)", out)

    def test_a_monorepo_package_reads_only_the_commits_of_its_folder(self):
        calls = []
        manifest = '{"packages/api": "1.4.2"}'
        status, _ = run(args(package="packages/api"), fake_git(manifest=manifest, calls=calls))
        self.assertEqual(status, 0)
        log = [c for c in calls if c[:2] == ("log", "-z")][0]
        self.assertEqual(log[-2:], ("--", "packages/api"))
        calls.clear()
        run(args(), fake_git(calls=calls))
        self.assertNotIn("--", [c for c in calls if c[:2] == ("log", "-z")][0])

    def test_a_shallow_checkout_is_an_error(self):
        status, out = run(args(), fake_git(shallow=True))
        self.assertEqual(status, 1)
        self.assertIn("the checkout is shallow", out)

    def test_a_sha256_head_is_accepted(self):
        status, _ = run(args(head_sha="b" * 64), fake_git())
        self.assertEqual(status, 0)

    def test_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out")
            status, _ = run(args(github_output=path), fake_git(commits=[("1" * 40, 5, "feat: old")]))
            self.assertEqual(status, 1)
            self.assertEqual(Path(path).read_text(encoding="utf-8"),
                             f"version=1.4.2\ntag=v1.4.2\nrelease-sha={RELEASE}\nresult=fail\nfindings=1\n")


class DryRunTest(unittest.TestCase):
    def test_reads_the_fixture_and_reads_no_git(self):
        def no_git(*argv):
            raise AssertionError(f"git called: {argv}")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out")
            status, out = run(args(dry_run="true", base_ref="", head_sha="", workspace=str(FIXTURES / "basic"),
                                   github_output=path), no_git)
            self.assertEqual(status, 0)
            self.assertIn("dry run", out)
            self.assertEqual(Path(path).read_text(encoding="utf-8"),
                             (FIXTURES / "expected" / "basic.env").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
