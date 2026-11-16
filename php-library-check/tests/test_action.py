"""Tests of the wiring in action.yml.

Checks that PHP, Composer and the install come only from php-composer-setup
and the audit only from php-composer-scan, both as dry runs in a dry run,
that each step passes only inputs that its action declares, that
composer-auth reaches only the setup step, that the tool cache is opt-in
and keyed after the install, from the plan and the installed dependencies,
and the order of the steps.

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
CACHE_STEP = "Restore the caches of the static check tools"
CACHE_KEY_STEP = "Build the tool cache key from the installed dependencies"
PLAN_STEP = "Check the inputs and plan the checks"
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

    def test_setup_maps_the_dependency_inputs(self):
        step = self.steps[SETUP_STEP]
        self.assertEqual(step["id"], "setup")
        self.assertEqual(step["with"]["dependency-versions"], "${{ inputs.dependency-versions }}")
        self.assertEqual(step["with"]["unset-platform-php"], "${{ inputs.unset-platform-php }}")

    def test_expect_majors_reaches_only_the_plan_step(self):
        plan = self.steps["Check the inputs and plan the checks"]
        self.assertEqual(plan["env"]["EXPECT_MAJORS"], "${{ inputs.expect-majors }}")
        for name, step in self.steps.items():
            if name == "Check the inputs and plan the checks":
                continue
            values = " ".join([*step["env"].values(), *step["with"].values()])
            with self.subTest(step=name):
                self.assertNotIn("inputs.expect-majors", values)

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

    def test_tool_cache_uses_actions_cache_pinned_by_sha(self):
        self.assertRegex(self.steps[CACHE_STEP]["uses"], r"^actions/cache@[0-9a-f]{40} # v\d+\.\d+\.\d+$")

    def test_tool_cache_key_runs_only_when_planned_and_not_in_a_dry_run(self):
        step = self.steps[CACHE_KEY_STEP]
        self.assertEqual(step["id"], "tool-cache")
        self.assertEqual(step["if"], "${{ inputs.dry-run != 'true' && steps.plan.outputs.tool-cache-prefix != '' }}")
        self.assertEqual(step["env"], {"PLAN_FILE": "${{ steps.plan.outputs.plan-file }}"})
        self.assertIn('check.py" cache-key', step["run"])

    def test_tool_cache_runs_only_with_a_key_and_not_in_a_dry_run(self):
        self.assertEqual(
            self.steps[CACHE_STEP]["if"],
            "${{ inputs.dry-run != 'true' && steps.tool-cache.outputs.key != '' }}",
        )

    def test_tool_cache_takes_its_values_from_the_plan_and_the_key_step_only(self):
        self.assertEqual(self.steps[CACHE_STEP]["with"], {
            "path": "${{ steps.plan.outputs.tool-cache-path-lines }}",
            "key": "${{ steps.tool-cache.outputs.key }}",
            "restore-keys": "${{ steps.tool-cache.outputs.restore-keys }}",
        })

    def test_tool_cache_paths_reaches_only_the_plan_step(self):
        self.assertEqual(self.steps[PLAN_STEP]["env"]["TOOL_CACHE_PATHS"], "${{ inputs.tool-cache-paths }}")
        for name, step in self.steps.items():
            if name == PLAN_STEP:
                continue
            values = " ".join([*step["env"].values(), *step["with"].values()])
            with self.subTest(step=name):
                self.assertNotIn("inputs.tool-cache-paths", values)

    def test_tool_cache_key_follows_the_install_and_precedes_the_restore(self):
        names = list(self.steps)
        self.assertLess(names.index(SETUP_STEP), names.index(AUDIT_STEP))
        self.assertLess(names.index(AUDIT_STEP), names.index(CACHE_KEY_STEP))
        self.assertLess(names.index(CACHE_KEY_STEP), names.index(CACHE_STEP))
        self.assertLess(names.index(CACHE_STEP), names.index(RUN_STEP))


if __name__ == "__main__":
    unittest.main()
