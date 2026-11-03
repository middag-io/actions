"""Tests of the wiring in action.yml and of scripts/composer-install.sh.

The token of the GitHub App exists only after every build step, and a dry run
never creates it nor uploads. The steps are read with a small reader for the
layout of action.yml (two-space indentation, "- name:" first in each step),
standard library only.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ACTION = os.path.join(COMPONENT, "action.yml")
COMPOSER_INSTALL = os.path.join(COMPONENT, "scripts", "composer-install.sh")
STEP_RE = re.compile(r"^    - name: (.+)$")
KEY_RE = re.compile(r"^      ([a-z-]+):\s*(.*)$")
ENV_RE = re.compile(r"^        ([A-Z0-9_]+): (.*)$")
SECRET_INPUT_RE = re.compile(r"inputs\.(app-private-key|composer-auth)\b")

VALIDATE = "Check the inputs"
COMPOSER = "Install the Composer dependencies"
ARCHIVE = "Build the archive"
TOKEN = "Create the GitHub App token"
UPLOAD = "Upload the archive to the release"


def read_steps(path):
    """Returns {step name: {"id", "if", "uses", "env": {...}, "with": text, "run": text}}."""
    steps, step, key = {}, None, None
    with open(path, encoding="utf-8") as handle:
        for line in handle.read().splitlines():
            match = STEP_RE.match(line)
            if match:
                step = {"id": "", "if": "", "uses": "", "env": {}, "with": "", "run": ""}
                steps[match.group(1)] = step
                key = None
                continue
            if step is None:
                continue
            match = KEY_RE.match(line)
            if match:
                key = match.group(1)
                if key in ("id", "if", "uses"):
                    step[key] = match.group(2)
                continue
            if key == "env":
                match = ENV_RE.match(line)
                if match:
                    step["env"][match.group(1)] = match.group(2)
            elif key in ("run", "with"):
                step[key] += line.strip() + "\n"
    return steps


class WiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.steps = read_steps(ACTION)
        cls.names = list(cls.steps)

    def test_validation_runs_first_in_every_mode(self):
        self.assertEqual(self.names[0], VALIDATE)
        self.assertEqual(self.steps[VALIDATE]["if"], "")
        self.assertIn('wp_plugin_release.py" validate', self.steps[VALIDATE]["run"])

    def test_token_and_upload_come_last_and_skip_a_dry_run(self):
        self.assertEqual(self.names[-2:], [TOKEN, UPLOAD])
        self.assertLess(self.names.index(ARCHIVE), self.names.index(TOKEN))
        for name in (TOKEN, UPLOAD):
            with self.subTest(step=name):
                self.assertEqual(self.steps[name]["if"], "${{ inputs.dry-run != 'true' }}")

    def test_token_is_narrowed_to_the_calling_repository(self):
        text = self.steps[TOKEN]["with"]
        self.assertIn("repositories: ${{ github.event.repository.name }}", text)
        self.assertIn("permission-contents: write", text)

    def test_app_private_key_reaches_only_the_token_step(self):
        for name, step in self.steps.items():
            if name == TOKEN:
                continue
            values = list(step["env"].values()) + [step["with"], step["run"]]
            with self.subTest(step=name):
                self.assertFalse(any("inputs.app-private-key" in v for v in values if name != VALIDATE))

    def test_composer_auth_reaches_only_validation_and_composer(self):
        for name, step in self.steps.items():
            values = list(step["env"].values()) + [step["with"], step["run"]]
            uses_auth = any("inputs.composer-auth" in v for v in values)
            with self.subTest(step=name):
                self.assertEqual(uses_auth, name in (VALIDATE, COMPOSER))

    def test_no_expression_inside_run(self):
        for name, step in self.steps.items():
            with self.subTest(step=name):
                self.assertNotIn("${{", step["run"])

    def test_upload_reads_the_token_and_the_archive(self):
        self.assertEqual(
            self.steps[UPLOAD]["env"],
            {
                "GH_TOKEN": "${{ steps.app-token.outputs.token }}",
                "TAG_NAME": "${{ inputs.tag-name }}",
                "ARCHIVE_PATH": "${{ steps.archive.outputs.archive-path }}",
            },
        )

    def test_third_party_actions_are_pinned(self):
        for name, step in self.steps.items():
            if step["uses"]:
                with self.subTest(step=name):
                    self.assertRegex(step["uses"], r"^[\w.-]+/[\w.-]+@[0-9a-f]{40} # v?\d+\.\d+\.\d+$")


FAKE_COMPOSER = """#!/usr/bin/env bash
echo "composer $*" >> "$CALLS"
"""
FAKE_PHP = """#!/usr/bin/env bash
echo "php $*" >> "$CALLS"
"""


class ComposerInstallTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.bin = os.path.join(self.dir, "bin")
        self.work = os.path.join(self.dir, "work")
        os.makedirs(self.bin)
        os.makedirs(self.work)
        for name, body in (("composer", FAKE_COMPOSER), ("php", FAKE_PHP)):
            full = os.path.join(self.bin, name)
            with open(full, "w", encoding="utf-8") as handle:
                handle.write(body)
            os.chmod(full, stat.S_IRWXU)
        self.calls = os.path.join(self.dir, "calls.txt")

    def run_script(self, has_strauss):
        env = {
            "PATH": self.bin + os.pathsep + os.environ.get("PATH", ""),
            "CALLS": self.calls,
            "HAS_STRAUSS": has_strauss,
        }
        result = subprocess.run(
            ["bash", COMPOSER_INSTALL], cwd=self.work, env=env, capture_output=True, text=True, check=False
        )
        calls = ""
        if os.path.exists(self.calls):
            with open(self.calls, encoding="utf-8") as handle:
                calls = handle.read()
        return result, calls.splitlines()

    def test_without_composer_json_does_nothing(self):
        result, calls = self.run_script("false")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [])

    def test_production_install(self):
        open(os.path.join(self.work, "composer.json"), "w").close()
        result, calls = self.run_script("false")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            calls,
            ["composer --version", "composer install --no-dev --optimize-autoloader --no-interaction --no-progress"],
        )

    def test_strauss_then_reinstall_without_dev_and_strip_tokens(self):
        open(os.path.join(self.work, "composer.json"), "w").close()
        os.makedirs(os.path.join(self.work, "scripts"))
        open(os.path.join(self.work, "scripts", "strip-lock-tokens.php"), "w").close()
        result, calls = self.run_script("true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            calls,
            [
                "composer --version",
                "composer install --optimize-autoloader --no-interaction --no-progress",
                "composer strauss --no-interaction",
                "composer install --no-dev --optimize-autoloader --no-scripts --no-interaction --no-progress",
                "composer dump-autoload --optimize --no-interaction",
                "php scripts/strip-lock-tokens.php",
            ],
        )


if __name__ == "__main__":
    unittest.main()
