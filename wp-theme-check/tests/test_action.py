"""Tests of the wiring in action.yml and in the reusable workflow.

Checks that the dry run skips every step that runs a tool and reaches
php-composer-setup as a dry run, that the password reaches only the
php-composer-setup step, that this step passes only inputs that
php-composer-setup declares, and that the reusable workflow passes every input
of the composite action.

The files are read with small readers for their layout (two-space
indentation), standard library only.

Run: python3 -I -m unittest discover -s tests -v (from the component folder).
"""

import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ACTION = os.path.join(COMPONENT, "action.yml")
SETUP_ACTION = os.path.join(os.path.dirname(COMPONENT), "php-composer-setup", "action.yml")
SETUP_STEP = "Set up PHP and install the Composer dependencies"
WORKFLOW = os.path.join(os.path.dirname(COMPONENT), ".github", "workflows", "wp-theme-check.yml")
STEP_RE = re.compile(r"^    - name: (.+)$")
KEY_RE = re.compile(r"^      ([a-z-]+):\s*(.*)$")
INPUT_RE = re.compile(r"^  ([a-z-]+):$")


def read_steps(path):
    """Returns {step name: {key: first-line value, "env": [names], "with": {key: value}}}."""
    steps, step, key = {}, None, None
    with open(path, encoding="utf-8") as handle:
        for line in handle.read().splitlines():
            match = STEP_RE.match(line)
            if match:
                step = {"env": [], "with": {}}
                steps[match.group(1)] = step
                continue
            if step is None:
                continue
            match = KEY_RE.match(line)
            if match:
                key = match.group(1)
                if key not in ("env", "with"):
                    step[key] = match.group(2)
            elif key == "env" and line.startswith("        "):
                step["env"].append(line.strip().split(":", 1)[0])
            elif key == "with" and line.startswith("        "):
                name, value = line.strip().split(":", 1)
                step["with"][name] = value.strip()
    return steps


def action_inputs(path=ACTION):
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

    def test_check_step_runs_first_and_always(self):
        first = next(iter(self.steps))
        self.assertEqual(first, "Check the inputs")
        self.assertNotIn("if", self.steps[first])
        self.assertEqual(self.steps[first]["id"], "plan")

    def test_dry_run_skips_every_tool_step(self):
        for name, step in self.steps.items():
            # The audit step runs php-composer-scan, which only validates
            # and reports in a dry run (tests/test_audit.py).
            if name in ("Check the inputs", SETUP_STEP, "Audit the Composer dependencies"):
                continue
            with self.subTest(step=name):
                self.assertIn("inputs.dry-run != 'true'", step.get("if", ""))

    def test_setup_step_runs_php_composer_setup_as_a_dry_run_in_a_dry_run(self):
        step = self.steps[SETUP_STEP]
        self.assertEqual(step["uses"], "$/php-composer-setup")
        self.assertNotIn("if", step)
        self.assertEqual(step["with"]["dry-run"], "${{ inputs.dry-run }}")

    def test_setup_step_passes_only_inputs_of_php_composer_setup(self):
        self.assertLessEqual(set(self.steps[SETUP_STEP]["with"]), set(action_inputs(SETUP_ACTION)))

    def test_setup_step_maps_the_composer_auth_inputs(self):
        passed = self.steps[SETUP_STEP]["with"]
        self.assertEqual(passed["http-basic-host"], "${{ inputs.composer-auth-host }}")
        self.assertEqual(passed["http-basic-username"], "${{ inputs.composer-auth-username }}")
        self.assertEqual(passed["http-basic-password"], "${{ inputs.composer-auth-password }}")
        self.assertEqual(passed["working-directory"], "${{ inputs.working-directory }}")

    def test_password_reaches_only_the_setup_step(self):
        for name, step in self.steps.items():
            with self.subTest(step=name):
                self.assertNotIn("COMPOSER_AUTH_PASSWORD", step["env"])
                if name != SETUP_STEP:
                    self.assertNotIn("composer-auth-password", " ".join(step["with"].values()))

    def test_tools_run_in_the_theme_directory(self):
        for name in ("Run PHP CS Fixer", "Run PHPStan", "Run Rector"):
            with self.subTest(step=name):
                self.assertEqual(self.steps[name]["working-directory"], "${{ inputs.working-directory }}")

    def test_tools_run_read_only(self):
        self.assertEqual(self.steps["Run PHP CS Fixer"]["run"], "vendor/bin/php-cs-fixer fix --dry-run --diff")
        self.assertEqual(self.steps["Run Rector"]["run"], "vendor/bin/rector process --dry-run")


class WorkflowWiringTest(unittest.TestCase):
    def test_workflow_passes_every_action_input(self):
        with open(WORKFLOW, encoding="utf-8") as handle:
            text = handle.read()
        step = text.split("uses: $/wp-theme-check", 1)[1]
        passed = re.findall(r"^          ([a-z-]+):", step, flags=re.MULTILINE)
        self.assertEqual(sorted(passed), sorted(action_inputs()))

    def test_workflow_drops_the_password_in_a_dry_run(self):
        with open(WORKFLOW, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("composer-auth-password: ${{ !inputs.dry-run && secrets.composer-auth-password || '' }}", text)


if __name__ == "__main__":
    unittest.main()
