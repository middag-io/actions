"""Tests of the wiring in action.yml.

Checks that PHP, Composer and the install of the code under test come only
from php-composer-setup, which runs as a dry run in a dry run, that this step
passes only inputs that php-composer-setup declares, and that composer-auth
reaches only that step.

The file is read with a small reader for its layout (two-space indentation),
standard library only.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ACTION = os.path.join(COMPONENT, "action.yml")
SETUP_ACTION = os.path.join(os.path.dirname(COMPONENT), "php-composer-setup", "action.yml")
SETUP_STEP = "Set up PHP and install the code under test"
RUN_STEP = "Install the Moodle host and run the probe"
STEP_RE = re.compile(r"^    - name: (.+)$")
KEY_RE = re.compile(r"^      ([a-z-]+):\s*(.*)$")
INPUT_RE = re.compile(r"^  ([a-z-]+):$")


def read_steps(path):
    """Returns {step name: {key: first-line value, "env": {name: value}, "with": {key: value}}}."""
    steps, step, key = {}, None, None
    with open(path, encoding="utf-8") as handle:
        for line in handle.read().splitlines():
            match = STEP_RE.match(line)
            if match:
                step = {"env": {}, "with": {}}
                steps[match.group(1)] = step
                continue
            if step is None or line.strip().startswith("#"):
                continue
            match = KEY_RE.match(line)
            if match:
                key = match.group(1)
                if key not in ("env", "with"):
                    step[key] = match.group(2)
            elif key in ("env", "with") and line.startswith("        "):
                name, value = line.strip().split(":", 1)
                step[key][name] = value.strip()
    return steps


def action_inputs(path):
    names, inside = [], False
    with open(path, encoding="utf-8") as handle:
        for line in handle.read().splitlines():
            if line == "inputs:":
                inside = True
            elif inside and line and not line.startswith(" "):
                break
            elif inside:
                match = INPUT_RE.match(line)
                if match:
                    names.append(match.group(1))
    return names


class ActionWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.steps = read_steps(ACTION)

    def test_no_step_calls_setup_php_directly(self):
        for name, step in self.steps.items():
            with self.subTest(step=name):
                self.assertNotIn("shivammathur/setup-php", step.get("uses", ""))

    def test_setup_step_runs_php_composer_setup_with_install(self):
        step = self.steps[SETUP_STEP]
        self.assertEqual(step["uses"], "$/php-composer-setup")
        self.assertNotIn("install", step["with"])
        self.assertNotIn("if", step)
        self.assertEqual(step["with"]["dry-run"], "${{ inputs.dry-run }}")

    def test_setup_step_passes_only_inputs_of_php_composer_setup(self):
        self.assertLessEqual(set(self.steps[SETUP_STEP]["with"]), set(action_inputs(SETUP_ACTION)))

    def test_setup_step_maps_the_inputs(self):
        passed = self.steps[SETUP_STEP]["with"]
        self.assertEqual(passed["php-version"], "${{ inputs.php-version }}")
        self.assertEqual(passed["php-extensions"], "${{ inputs.php-extensions }}")
        self.assertEqual(passed["working-directory"], "${{ inputs.path }}")
        self.assertEqual(passed["composer-auth"], "${{ inputs.composer-auth }}")

    def test_composer_auth_reaches_only_the_setup_step(self):
        for name, step in self.steps.items():
            if name == SETUP_STEP:
                continue
            values = " ".join([*step["env"].values(), *step["with"].values()])
            with self.subTest(step=name):
                self.assertNotIn("inputs.composer-auth", values)

    def test_host_step_skips_a_dry_run_and_runs_after_the_setup(self):
        names = list(self.steps)
        self.assertLess(names.index(SETUP_STEP), names.index(RUN_STEP))
        self.assertEqual(self.steps[RUN_STEP]["if"], "${{ inputs.dry-run != 'true' }}")


if __name__ == "__main__":
    unittest.main()
