"""Tests of the wiring in action.yml and in the reusable workflow.

Checks that every input reaches the script only through env: (never inside
run:), that every env: value comes from an input of the action, and that the
reusable workflow passes the base branch, the head SHA and the full history
outside a dry run.

The files are read as text, standard library only.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ACTION = os.path.join(COMPONENT, "action.yml")
WORKFLOW = os.path.join(os.path.dirname(COMPONENT), ".github", "workflows", "github-release-date-check.yml")
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
        self.assertEqual(env["BASE_REF"], "base-ref")


class WorkflowWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read(WORKFLOW)

    def test_permissions_are_contents_read_only(self):
        self.assertIn("permissions: {}", self.text)
        self.assertNotIn("pull-requests:", self.text)
        self.assertNotIn(": write", self.text)

    def test_full_history_base_branch_and_head_sha_outside_a_dry_run(self):
        self.assertIn("ref: ${{ !inputs.dry-run && github.event.pull_request.head.sha || '' }}", self.text)
        self.assertIn("fetch-depth: ${{ inputs.dry-run && 1 || 0 }}", self.text)
        self.assertIn("base-ref: ${{ !inputs.dry-run && github.event.pull_request.base.ref || '' }}", self.text)
        self.assertIn("head-sha: ${{ !inputs.dry-run && github.event.pull_request.head.sha || '' }}", self.text)
        self.assertIn("persist-credentials: false", self.text)
        # The base is the tip of the base branch when the check runs, never the base SHA of the event.
        self.assertNotIn("pull_request.base.sha", self.text)

    def test_concurrency_group_holds_the_run_attempt_and_the_package(self):
        self.assertIn(
            "group: github-release-date-check-${{ github.run_id }}-${{ github.run_attempt }}"
            "-${{ inputs.package == '.' && 'root' || inputs.package }}", self.text)
        self.assertIn("cancel-in-progress: false", self.text)

    def test_reads_no_variable(self):
        self.assertNotIn("vars.", self.text)


if __name__ == "__main__":
    unittest.main()
