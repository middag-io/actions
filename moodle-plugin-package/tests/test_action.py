"""Tests of the wiring in action.yml.

A dry run must skip every step that installs, uploads or uses a credential,
except php-composer-setup, which runs as a dry run, and the credentials must
reach only the steps that use them. PHP and Composer come only from
php-composer-setup, which gets only inputs that it declares. The steps are
read with a small reader for the layout of action.yml (two-space indentation,
"- name:" first in each step), standard library only.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ACTION = os.path.join(COMPONENT, "action.yml")
SETUP_ACTION = os.path.join(os.path.dirname(COMPONENT), "php-composer-setup", "action.yml")
SETUP_STEP = "Set up PHP and Composer"
INPUT_RE = re.compile(r"^  ([a-z-]+):$")
WITH_RE = re.compile(r"^        ([a-z-]+): (.*)$")
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
                step = {"if": "", "uses": "", "env": {}, "with": {}}
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
            match = WITH_RE.match(line)
            if key == "with" and match:
                step["with"][match.group(1)] = match.group(2)
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
            if name in ALWAYS_RUN or name == SETUP_STEP:
                continue
            with self.subTest(step=name):
                self.assertIn(DRY_RUN_GUARD, step["if"])

    def test_setup_step_runs_php_composer_setup_as_a_dry_run_without_install(self):
        step = self.steps[SETUP_STEP]
        self.assertEqual(step["uses"], "$/php-composer-setup")
        self.assertEqual(step["if"], "${{ steps.check.outputs.has-composer == 'true' }}")
        self.assertEqual(step["with"]["dry-run"], "${{ steps.check.outputs.dry-run }}")
        self.assertEqual(step["with"]["install"], '"false"')

    def test_setup_step_passes_only_inputs_of_php_composer_setup(self):
        self.assertLessEqual(set(self.steps[SETUP_STEP]["with"]), set(action_inputs(SETUP_ACTION)))

    def test_composer_auth_reaches_only_the_setup_step(self):
        for name, step in self.steps.items():
            values = " ".join([*step["env"].values(), *step["with"].values()])
            with self.subTest(step=name):
                if name == SETUP_STEP:
                    self.assertEqual(step["with"]["composer-auth"], "${{ inputs.composer-auth }}")
                else:
                    self.assertNotIn("inputs.composer-auth", values)

    def test_no_step_calls_setup_php_directly(self):
        for name, step in self.steps.items():
            with self.subTest(step=name):
                self.assertNotIn("shivammathur/setup-php", step["uses"])

    def test_credentials_reach_only_the_steps_that_use_them(self):
        holders = {
            name for name, step in self.steps.items()
            if any("inputs.github-token" in v for v in step["env"].values())
        }
        self.assertEqual(holders, {
            "Check access to the GitHub Packages dependencies",
            "Install the npm dependencies",
            "Upload the ZIP to the GitHub release",
        })

    def test_install_keeps_the_job_composer_auth(self):
        # A step-level COMPOSER_AUTH, even empty, would hide one that the
        # caller sets in the job or workflow env.
        self.assertNotIn("COMPOSER_AUTH", self.steps["Install the Composer dependencies"]["env"])

    def test_actions_are_pinned_by_sha(self):
        for name, step in self.steps.items():
            if step["uses"] and not step["uses"].startswith("$/"):
                with self.subTest(step=name):
                    self.assertRegex(step["uses"], r"^[\w.-]+/[\w.-]+@[0-9a-f]{40} # \S+$")


if __name__ == "__main__":
    unittest.main()
