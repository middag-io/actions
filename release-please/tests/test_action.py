"""Tests of the wiring in action.yml.

The check step validates everything and runs in every mode without a secret;
the token and release-please steps are skipped in a dry run, and only the
token step reads the private key. These tests keep that split, so that a
dry run never sees a secret and a run never skips the check.

The steps are read with a small reader for the layout of action.yml (two-space
indentation, "- name:" first in each step), standard library only.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ACTION = os.path.join(os.path.dirname(HERE), "action.yml")
STEP_RE = re.compile(r"^    - name: (.+)$")
KEY_RE = re.compile(r"^      ([a-z-]+):\s*(.*)$")
NESTED_RE = re.compile(r"^        ([A-Za-z0-9_-]+): (.*)$")
SHA_PIN_RE = re.compile(r"^[a-z0-9-]+/[a-z0-9-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+$")
CHECK = "Check the inputs, the configuration and the manifest"
TOKEN = "Create the GitHub App token"
RELEASE = "Run release-please"
SKIP_IN_DRY_RUN = "${{ inputs.dry-run != 'true' }}"


def read_steps(path):
    """Returns {step name: {"id", "if", "uses", "env": {...}, "with": {...}, "run"}}."""
    steps, step, key = {}, None, None
    with open(path, encoding="utf-8") as handle:
        for line in handle.read().splitlines():
            match = STEP_RE.match(line)
            if match:
                step = {"id": "", "if": "", "uses": "", "env": {}, "with": {}, "run": ""}
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
            if key in ("env", "with"):
                match = NESTED_RE.match(line)
                if match:
                    step[key][match.group(1)] = match.group(2)
            elif key == "run":
                step["run"] += line.strip() + "\n"
    return steps


class WiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.steps = read_steps(ACTION)

    def test_three_steps_in_order(self):
        self.assertEqual(list(self.steps), [CHECK, TOKEN, RELEASE])
        self.assertEqual(self.steps[CHECK]["id"], "check")

    def test_check_runs_in_every_mode_without_secrets(self):
        check = self.steps[CHECK]
        self.assertEqual(check["if"], "")
        for name, value in check["env"].items():
            with self.subTest(name=name):
                self.assertNotRegex(value, r"inputs\.app-private-key *\}\}")
        self.assertIn("check_config.py", check["run"])

    def test_token_and_release_are_skipped_in_a_dry_run(self):
        self.assertEqual(self.steps[TOKEN]["if"], SKIP_IN_DRY_RUN)
        self.assertEqual(self.steps[RELEASE]["if"], SKIP_IN_DRY_RUN)

    def test_only_the_token_step_reads_the_private_key(self):
        self.assertEqual(self.steps[TOKEN]["with"]["private-key"], "${{ inputs.app-private-key }}")
        for name in (CHECK, RELEASE):
            with self.subTest(step=name):
                step = self.steps[name]
                values = list(step["env"].values()) + list(step["with"].values())
                self.assertFalse(any("inputs.app-private-key }}" in value for value in values))

    def test_release_uses_the_app_token(self):
        self.assertEqual(self.steps[RELEASE]["with"]["token"], "${{ steps.app-token.outputs.token }}")

    def test_third_party_actions_are_pinned_by_sha(self):
        for name in (TOKEN, RELEASE):
            with self.subTest(step=name):
                self.assertRegex(self.steps[name]["uses"], SHA_PIN_RE)


if __name__ == "__main__":
    unittest.main()
