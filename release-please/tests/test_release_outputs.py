"""Tests of scripts/release_outputs.py. Standard library only, no network.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import release_outputs  # noqa: E402

# Outputs of release-please-action v5 for a run that released the root
# package and packages/cli, and updated one release pull request. The pr and
# prs values are JSON strings, as the action sets them.
PR = {
    "headBranchName": "release-please--branches--main",
    "baseBranchName": "main",
    "number": 42,
    "title": "chore: release main",
    "body": "notes",
    "labels": [],
    "files": [],
}
RELEASE_AND_PR = {
    "releases_created": "true",
    "paths_released": '[".","packages/cli"]',
    "release_created": "true",
    "tag_name": "v2.0.0",
    "version": "2.0.0",
    "packages/cli--release_created": "true",
    "packages/cli--tag_name": "cli-v1.4.0",
    "packages/cli--version": "1.4.0",
    "prs_created": "true",
    "pr": json.dumps(PR),
    "prs": json.dumps([PR]),
}


class BuildOutputsTest(unittest.TestCase):
    def test_lists_each_release_with_its_tag_and_version(self):
        outputs = release_outputs.build_outputs(RELEASE_AND_PR)
        self.assertEqual(
            json.loads(outputs["releases"]),
            [
                {"path": ".", "tag-name": "v2.0.0", "version": "2.0.0"},
                {"path": "packages/cli", "tag-name": "cli-v1.4.0", "version": "1.4.0"},
            ],
        )

    def test_reports_the_release_pull_request(self):
        outputs = release_outputs.build_outputs(RELEASE_AND_PR)
        self.assertEqual(outputs["release-pr-created"], "true")
        self.assertEqual(outputs["release-pr-branch"], "release-please--branches--main")
        self.assertEqual(outputs["release-pr-number"], "42")
        self.assertEqual(json.loads(outputs["release-prs"]), [{"branch": "release-please--branches--main", "number": 42}])

    def test_lists_every_release_pull_request(self):
        other = {**PR, "headBranchName": "release-please--branches--main--components--cli", "number": 43}
        outputs = release_outputs.build_outputs({"prs_created": "true", "prs": json.dumps([PR, other])})
        self.assertEqual(outputs["release-pr-number"], "42")
        self.assertEqual([pr["number"] for pr in json.loads(outputs["release-prs"])], [42, 43])

    def test_run_without_release_or_pull_request(self):
        outputs = release_outputs.build_outputs({"releases_created": "false", "paths_released": "[]", "prs_created": "false"})
        self.assertEqual(
            outputs,
            {
                "releases": "[]",
                "release-pr-created": "false",
                "release-pr-branch": "",
                "release-pr-number": "",
                "release-prs": "[]",
            },
        )

    def test_empty_outputs_give_the_empty_values(self):
        outputs = release_outputs.build_outputs({})
        self.assertEqual(outputs["releases"], "[]")
        self.assertEqual(outputs["release-pr-created"], "false")
        self.assertEqual(outputs["release-prs"], "[]")

    def test_ignores_prs_when_prs_created_is_false(self):
        outputs = release_outputs.build_outputs({"prs_created": "false", "prs": json.dumps([PR])})
        self.assertEqual(outputs["release-pr-created"], "false")
        self.assertEqual(outputs["release-pr-branch"], "")

    def test_rejects_invalid_paths_released(self):
        with self.assertRaisesRegex(release_outputs.OutputsError, "paths_released"):
            release_outputs.build_outputs({"paths_released": "not json"})

    def test_rejects_a_pull_request_without_number(self):
        broken = {key: value for key, value in PR.items() if key != "number"}
        with self.assertRaisesRegex(release_outputs.OutputsError, "without number"):
            release_outputs.build_outputs({"prs_created": "true", "prs": json.dumps([broken])})


class MainTest(unittest.TestCase):
    def run_main(self, raw):
        with tempfile.NamedTemporaryFile("r", suffix=".out") as output:
            env = {"RELEASE_OUTPUTS": raw, "GITHUB_OUTPUT": output.name}
            printed = io.StringIO()
            with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(printed):
                code = release_outputs.main()
            with open(output.name, encoding="utf-8") as handle:
                return code, handle.read(), printed.getvalue()

    def test_writes_the_outputs(self):
        code, written, _ = self.run_main(json.dumps(RELEASE_AND_PR))
        self.assertEqual(code, 0)
        self.assertIn("release-pr-created=true\n", written)
        self.assertIn("release-pr-number=42\n", written)
        self.assertIn('releases=[{"path":".","tag-name":"v2.0.0","version":"2.0.0"},', written)

    def test_fails_on_outputs_that_are_not_a_json_object(self):
        code, written, printed = self.run_main("[]")
        self.assertEqual(code, 1)
        self.assertEqual(written, "")
        self.assertIn("::error::", printed)


if __name__ == "__main__":
    unittest.main()
