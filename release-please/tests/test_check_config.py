"""Tests of scripts/check_config.py.

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
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "scripts", "check_config.py")
FIXTURE = os.path.join(HERE, "fixtures", "basic")
CONFIG = "release-please-config.json"
MANIFEST = ".release-please-manifest.json"

SPEC = importlib.util.spec_from_file_location("check_config", SCRIPT)
check_config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_config)


class CheckConfigTest(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.workspace = tempfile.mkdtemp()
        self.copy_fixture()
        self.output = os.path.join(self.workspace, "github-output")
        os.chdir(self.workspace)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.workspace)

    def write_json(self, name, data):
        with open(name, "w", encoding="utf-8") as handle:
            json.dump(data, handle)

    def run_check(self, *extra, dry_run="true", client_id="false", private_key="false"):
        argv = [
            f"--config-file={CONFIG}",
            f"--manifest-file={MANIFEST}",
            f"--dry-run={dry_run}",
            f"--has-app-client-id={client_id}",
            f"--has-app-private-key={private_key}",
            *extra,
        ]
        stdout = io.StringIO()
        env = {"GITHUB_OUTPUT": self.output}
        with contextlib.redirect_stdout(stdout), unittest.mock.patch.dict(os.environ, env):
            status = check_config.main(argv)
        return status, stdout.getvalue()

    def read_outputs(self):
        if not os.path.exists(self.output):
            return {}
        with open(self.output, encoding="utf-8") as handle:
            return dict(line.rstrip("\n").split("=", 1) for line in handle)

    def test_dry_run_of_the_fixture_lists_the_packages(self):
        status, stdout = self.run_check()
        self.assertEqual(status, 0, stdout)
        self.assertEqual(self.read_outputs(), {"packages": '["."]'})
        self.assertIn("dry run: would run release-please", stdout)

    def test_packages_are_sorted(self):
        self.write_json(CONFIG, {"packages": {"tools/b": {}, ".": {}, "tools/a": {}}})
        status, _ = self.run_check()
        self.assertEqual(status, 0)
        self.assertEqual(self.read_outputs()["packages"], '[".","tools/a","tools/b"]')

    def test_package_missing_from_the_manifest_is_accepted(self):
        self.write_json(CONFIG, {"packages": {".": {}, "tools/a": {}}})
        status, stdout = self.run_check()
        self.assertEqual(status, 0, stdout)

    def test_credentials_are_required_outside_a_dry_run(self):
        status, stdout = self.run_check(dry_run="false", client_id="false", private_key="true")
        self.assertEqual(status, 1)
        self.assertIn("RELEASE_PLEASE_APP_CLIENT_ID", stdout)
        status, stdout = self.run_check(dry_run="false", client_id="true", private_key="false")
        self.assertEqual(status, 1)
        self.assertIn("RELEASE_PLEASE_APP_PRIVATE_KEY", stdout)
        self.assertEqual(self.read_outputs(), {})

    def test_credentials_present_outside_a_dry_run(self):
        status, stdout = self.run_check(dry_run="false", client_id="true", private_key="true")
        self.assertEqual(status, 0, stdout)
        self.assertIn("running release-please", stdout)

    def test_rejected_inputs(self):
        cases = {
            "absolute config path": [f"--config-file=/etc/{CONFIG}"],
            "config path outside the workspace": [f"--config-file=../{CONFIG}"],
            "invalid target branch": ["--target-branch=main..dev"],
            "target branch with a space": ["--target-branch=release branch"],
            "dry-run not a boolean": ["--dry-run=yes"],
        }
        for label, extra in cases.items():
            with self.subTest(label):
                status, stdout = self.run_check(*extra)
                self.assertEqual(status, 1, stdout)
                self.assertIn("::error::", stdout)

    def test_accepted_target_branches(self):
        for branch in ("main", "release/1.x", "MOODLE_405_STABLE"):
            with self.subTest(branch):
                status, stdout = self.run_check(f"--target-branch={branch}")
                self.assertEqual(status, 0, stdout)

    def test_rejected_files(self):
        cases = {
            "missing config": (CONFIG, None),
            "config not JSON": (CONFIG, "{not json"),
            "config not an object": (CONFIG, []),
            "config without packages": (CONFIG, {"release-type": "simple"}),
            "config with empty packages": (CONFIG, {"packages": {}}),
            "package path outside the repository": (CONFIG, {"packages": {"../x": {}}}),
            "package options not an object": (CONFIG, {"packages": {".": "simple"}}),
            "missing manifest": (MANIFEST, None),
            "manifest not an object": (MANIFEST, ["1.0.0"]),
            "manifest version not SemVer": (MANIFEST, {".": "v1.0"}),
            "manifest version not a string": (MANIFEST, {".": 1}),
            "manifest package not configured": (MANIFEST, {".": "1.0.0", "other": "1.0.0"}),
        }
        for label, (name, content) in cases.items():
            with self.subTest(label):
                self.copy_fixture()
                if content is None:
                    os.remove(name)
                elif isinstance(content, str):
                    with open(name, "w", encoding="utf-8") as handle:
                        handle.write(content)
                else:
                    self.write_json(name, content)
                status, stdout = self.run_check()
                self.assertEqual(status, 1, stdout)
                self.assertIn(f"::error::input {'config-file' if name == CONFIG else 'manifest-file'}", stdout)

    def test_prerelease_version_in_the_manifest(self):
        self.write_json(MANIFEST, {".": "2.0.0-rc.1"})
        status, stdout = self.run_check()
        self.assertEqual(status, 0, stdout)

    def copy_fixture(self):
        for name in (CONFIG, MANIFEST):
            shutil.copy(os.path.join(FIXTURE, name), self.workspace)


if __name__ == "__main__":
    unittest.main()
