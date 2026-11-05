"""Tests of the wiring in action.yml.

Checks that PHP, Composer and the install come only from php-composer-setup
and the audit only from php-composer-scan, both as dry runs in a dry run,
that each step passes only inputs that its action declares, that
composer-auth reaches only the setup step, and the order of the steps.

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
SETUP_STEP = "Set up PHP and install the Composer dependencies"
AUDIT_STEP = "Audit the Composer dependencies"
RUN_STEP = "Run the library checks"
SCAN_ACTION = os.path.join(os.path.dirname(COMPONENT), "php-composer-scan", "action.yml")
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

    def test_setup_and_audit_come_from_this_repository(self):
        self.assertEqual(self.steps[SETUP_STEP]["uses"], "$/php-composer-setup")
        self.assertEqual(self.steps[AUDIT_STEP]["uses"], "$/php-composer-scan")
        for name in (SETUP_STEP, AUDIT_STEP):
            with self.subTest(step=name):
                self.assertEqual(self.steps[name]["with"]["dry-run"], "${{ inputs.dry-run }}")
                self.assertNotIn("if", self.steps[name])

    def test_steps_pass_only_declared_inputs(self):
        self.assertLessEqual(set(self.steps[SETUP_STEP]["with"]), set(action_inputs(SETUP_ACTION)))
        self.assertLessEqual(set(self.steps[AUDIT_STEP]["with"]), set(action_inputs(SCAN_ACTION)))

    def test_audit_maps_the_blocking_severities(self):
        passed = self.steps[AUDIT_STEP]["with"]
        self.assertEqual(passed["blocking-severities"], "${{ inputs.audit-blocking-severities }}")
        self.assertEqual(passed["working-directory"], "${{ inputs.path }}")

    def test_composer_auth_reaches_only_the_setup_step(self):
        for name, step in self.steps.items():
            if name == SETUP_STEP:
                continue
            values = " ".join([*step["env"].values(), *step["with"].values()])
            with self.subTest(step=name):
                self.assertNotIn("inputs.composer-auth", values)

    def test_order_is_setup_audit_checks(self):
        names = list(self.steps)
        self.assertLess(names.index(SETUP_STEP), names.index(AUDIT_STEP))
        self.assertLess(names.index(AUDIT_STEP), names.index(RUN_STEP))


if __name__ == "__main__":
    unittest.main()
