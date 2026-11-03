"""Tests of the wiring in action.yml.

The plan step validates everything and is the only step that reads the
package; the publish step reads only the plan file and holds the only
credentials. These tests keep that split, so that a dry run never sees a
secret and a publication never skips the validation.

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
ENV_RE = re.compile(r"^        ([A-Z0-9_]+): (.*)$")
PLAN = "Check the inputs and plan the objects"
PUBLISH = "Publish the version and the manifest"


def read_steps(path):
    """Returns {step name: {"id", "if", "env": {...}, "run": text}}."""
    steps, step, key = {}, None, None
    with open(path, encoding="utf-8") as handle:
        for line in handle.read().splitlines():
            match = STEP_RE.match(line)
            if match:
                step = {"id": "", "if": "", "env": {}, "run": ""}
                steps[match.group(1)] = step
                key = None
                continue
            if step is None:
                continue
            match = KEY_RE.match(line)
            if match:
                key = match.group(1)
                if key in ("id", "if"):
                    step[key] = match.group(2)
                continue
            if key == "env":
                match = ENV_RE.match(line)
                if match:
                    step["env"][match.group(1)] = match.group(2)
            elif key == "run":
                step["run"] += line.strip() + "\n"
    return steps


class WiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.steps = read_steps(ACTION)

    def test_two_steps_in_order(self):
        self.assertEqual(list(self.steps), [PLAN, PUBLISH])
        self.assertEqual(self.steps[PLAN]["id"], "plan")

    def test_plan_runs_in_every_mode_without_secrets(self):
        plan = self.steps[PLAN]
        self.assertEqual(plan["if"], "")
        for name, value in plan["env"].items():
            with self.subTest(name=name):
                self.assertNotRegex(value, r"inputs\.(access-key-id|secret-access-key) *\}\}")
        self.assertIn('publish.py" plan', plan["run"])

    def test_plan_masks_the_endpoint_first(self):
        plan = self.steps[PLAN]
        self.assertEqual(plan["env"]["ENDPOINT"], "${{ inputs.endpoint }}")
        self.assertTrue(plan["run"].startswith('if [ -n "$ENDPOINT" ]; then\necho "::add-mask::$ENDPOINT"\n'))

    def test_publish_is_skipped_in_a_dry_run(self):
        self.assertEqual(self.steps[PUBLISH]["if"], "${{ inputs.dry-run != 'true' }}")

    def test_publish_reads_the_plan_and_the_secrets(self):
        env = self.steps[PUBLISH]["env"]
        self.assertEqual(
            env,
            {
                "PLAN_FILE": "${{ steps.plan.outputs.plan-file }}",
                "S3_ACCESS_KEY_ID": "${{ inputs.access-key-id }}",
                "S3_SECRET_ACCESS_KEY": "${{ inputs.secret-access-key }}",
            },
        )
        self.assertIn('publish.py" upload --plan-file="$PLAN_FILE"', self.steps[PUBLISH]["run"])

    def test_no_expression_in_run(self):
        for name, step in self.steps.items():
            with self.subTest(step=name):
                self.assertNotIn("${{", step["run"])


if __name__ == "__main__":
    unittest.main()
