"""Tests of the wiring in action.yml.

A dry run must skip every step that installs, uploads or uses a credential,
and the credentials must reach only the steps that use them. The steps are
read with a small reader for the layout of action.yml (two-space indentation,
"- name:" first in each step), standard library only.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ACTION = os.path.join(os.path.dirname(HERE), "action.yml")
STEP_RE = re.compile(r"^    - name: (.+)$")
KEY_RE = re.compile(r"^      ([a-z-]+):\s*(.*)$")
ENV_RE = re.compile(r"^        ([A-Z0-9_]+): (.*)$")
DRY_RUN_GUARD = "steps.check.outputs.dry-run != 'true'"
ALWAYS_RUN = ("Check the inputs and version.php", "Build the ZIP")


def read_steps(path):
    """Returns {step name: {"if", "uses", "env": {...}}} in file order."""
    steps, step, key = {}, None, None
    with open(path, encoding="utf-8") as handle:
        for line in handle.read().splitlines():
            match = STEP_RE.match(line)
            if match:
                step = {"if": "", "uses": "", "env": {}}
                steps[match.group(1)] = step
                key = None
                continue
            if step is None:
                continue
            match = KEY_RE.match(line)
            if match:
                key = match.group(1)
                if key in ("if", "uses"):
                    step[key] = match.group(2)
                continue
            match = ENV_RE.match(line)
            if key == "env" and match:
                step["env"][match.group(1)] = match.group(2)
    return steps


class WiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.steps = read_steps(ACTION)

    def test_check_runs_first_and_build_runs_always(self):
        names = list(self.steps)
        self.assertEqual(names[0], ALWAYS_RUN[0])
        for name in ALWAYS_RUN:
            self.assertEqual(self.steps[name]["if"], "", name)

    def test_every_other_step_is_skipped_in_a_dry_run(self):
        for name, step in self.steps.items():
            if name in ALWAYS_RUN:
                continue
            with self.subTest(step=name):
                self.assertIn(DRY_RUN_GUARD, step["if"])

    def test_credentials_reach_only_the_steps_that_use_them(self):
        holders = {
            name for name, step in self.steps.items()
            if any("inputs.github-token" in v or "inputs.composer-auth" in v for v in step["env"].values())
        }
        self.assertEqual(holders, {
            "Install the Composer dependencies",
            "Check access to the GitHub Packages dependencies",
            "Install the npm dependencies",
            "Upload the ZIP to the GitHub release",
        })

    def test_actions_are_pinned_by_sha(self):
        for name, step in self.steps.items():
            if step["uses"]:
                with self.subTest(step=name):
                    self.assertRegex(step["uses"], r"^[\w.-]+/[\w.-]+@[0-9a-f]{40} # \S+$")


if __name__ == "__main__":
    unittest.main()
