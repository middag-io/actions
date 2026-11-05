"""Tests of the wiring in action.yml and in the reusable workflow.

Checks that the title and the other inputs reach the script only through
env: (never inside run:), that every env: value comes from an input of the
action, and that the reusable workflow passes the pull request SHAs and the
full history outside a dry run.

The files are read as text, standard library only.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ACTION = os.path.join(COMPONENT, "action.yml")
WORKFLOW = os.path.join(os.path.dirname(COMPONENT), ".github", "workflows", "php-version-family-check.yml")
INPUT_RE = re.compile(r"^  ([a-z-]+):$", re.MULTILINE)
ENV_RE = re.compile(r"^        ([A-Z_]+): \$\{\{ inputs\.([a-z-]+) \}\}$", re.MULTILINE)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class ActionWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read(ACTION)
        cls.inputs = INPUT_RE.findall(cls.text.split("\noutputs:")[0])

    def test_run_block_holds_no_expression(self):
        run = self.text.split("      run: |", 1)[1]
        self.assertNotIn("${{", run)

    def test_every_input_reaches_the_script_through_env(self):
        env = dict(ENV_RE.findall(self.text))
        self.assertEqual(sorted(env.values()), sorted(self.inputs))
        self.assertEqual(env["PR_TITLE"], "title")


class WorkflowWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read(WORKFLOW)

    def test_permissions_are_contents_read_only(self):
        self.assertIn("permissions: {}", self.text)
        self.assertNotIn("pull-requests:", self.text)
        self.assertNotIn(": write", self.text)

    def test_full_history_and_pull_request_shas_outside_a_dry_run(self):
        self.assertIn("ref: ${{ !inputs.dry-run && github.event.pull_request.head.sha || '' }}", self.text)
        self.assertIn("fetch-depth: ${{ inputs.dry-run && 1 || 0 }}", self.text)
        self.assertIn("base-sha: ${{ !inputs.dry-run && github.event.pull_request.base.sha || '' }}", self.text)
        self.assertIn("head-sha: ${{ !inputs.dry-run && github.event.pull_request.head.sha || '' }}", self.text)
        self.assertIn("persist-credentials: false", self.text)

    def test_base_branch_and_window_branch_reach_the_action(self):
        self.assertIn("base-ref: ${{ !inputs.dry-run && github.event.pull_request.base.ref || '' }}", self.text)
        self.assertIn("window-branch: ${{ inputs.window-branch || github.event.repository.default_branch }}", self.text)

    def test_window_defaults_to_the_component_variable(self):
        self.assertIn("default: ${{ vars.PHP_VERSION_FAMILY_CHECK_WINDOW }}", self.text)


if __name__ == "__main__":
    unittest.main()
