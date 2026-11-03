"""Tests of the wiring in action.yml.

The plan step validates everything and holds no credential. Only the deploy
step receives the API token: the install and the build run the project's
code without it. The plan step masks the account id before anything else.
Every step after the plan is skipped in a dry run.

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
PLAN = "Check the inputs and plan the deployment"
SETUP = "Set up Node.js"
INSTALL = "Install the dependencies"
BUILD = "Build the Worker"
DEPLOY = "Deploy the Worker"
NOT_DRY_RUN = "inputs.dry-run != 'true'"


def read_steps(path):
    """Returns {step name: {"id", "if", "uses", "env": {...}, "run": text}}."""
    steps, step, key = {}, None, None
    with open(path, encoding="utf-8") as handle:
        for line in handle.read().splitlines():
            match = STEP_RE.match(line)
            if match:
                step = {"id": "", "if": "", "uses": "", "env": {}, "run": ""}
                steps[match.group(1)] = step
                key = None
                continue
            if step is None:
                continue
            match = KEY_RE.match(line)
            if match:
                key = match.group(1)
                if key in ("id", "if", "uses", "run"):
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

    def test_steps_in_order(self):
        self.assertEqual(list(self.steps), [PLAN, SETUP, INSTALL, BUILD, DEPLOY])

    def test_plan_runs_in_every_mode_without_the_token(self):
        plan = self.steps[PLAN]
        self.assertEqual(plan["id"], "plan")
        self.assertEqual(plan["if"], "")
        self.assertEqual(plan["env"]["HAS_API_TOKEN"], "${{ inputs.api-token != '' }}")
        for name, value in plan["env"].items():
            with self.subTest(name=name):
                self.assertNotRegex(value, r"inputs\.api-token *\}\}")

    def test_plan_masks_the_account_id_first(self):
        plan = self.steps[PLAN]
        self.assertEqual(plan["env"]["ACCOUNT_ID"], "${{ inputs.account-id }}")
        lines = plan["run"].removeprefix("|").splitlines()
        self.assertEqual(lines[:3], ['if [ -n "$ACCOUNT_ID" ]; then', 'echo "::add-mask::$ACCOUNT_ID"', "fi"])

    def test_every_later_step_is_skipped_in_a_dry_run(self):
        for name in (SETUP, INSTALL, BUILD, DEPLOY):
            with self.subTest(step=name):
                self.assertIn(NOT_DRY_RUN, self.steps[name]["if"])

    def test_only_the_deploy_step_holds_the_token(self):
        for name, step in self.steps.items():
            holds = any(re.search(r"inputs\.api-token *\}\}", value) for value in step["env"].values())
            with self.subTest(step=name):
                self.assertEqual(holds, name == DEPLOY)
        self.assertEqual(self.steps[DEPLOY]["env"]["CLOUDFLARE_API_TOKEN"], "${{ inputs.api-token }}")

    def test_no_dependency_cache(self):
        self.assertTrue(self.steps[SETUP]["uses"].startswith("actions/setup-node@"))
        with open(ACTION, encoding="utf-8") as handle:
            self.assertIn("package-manager-cache: false", handle.read())

    def test_no_expression_inside_run(self):
        for name, step in self.steps.items():
            with self.subTest(step=name):
                self.assertNotIn("${{", step["run"])


if __name__ == "__main__":
    unittest.main()
