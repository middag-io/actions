"""Tests of the dependency audit step in action.yml.

Checks that the audit comes only from php-composer-scan and node-npm-scan of this repository, as a dry
run in a dry run, that it passes only inputs that the scan action declares,
that audit-blocking-severities reaches it unchanged, and that no input turns
the audit off.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(COMPONENT)
ACTION = os.path.join(COMPONENT, "action.yml")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", os.path.basename(COMPONENT) + ".yml")
# Audit step name -> (scan action, expected working-directory, expected if)
AUDITS = {
    'Audit the Composer dependencies': ('php-composer-scan', '${{ inputs.path }}', "${{ inputs.target == 'php' }}"),
    'Audit the npm dependencies': ('node-npm-scan', '${{ inputs.path }}/${{ inputs.ui-directory }}', "${{ inputs.target == 'ui' }}"),
}
STEP_RE = re.compile(r"^    - name: (.+)$")
KEY_RE = re.compile(r"^      ([a-z-]+):\s*(.*)$")
INPUT_RE = re.compile(r"^  ([a-z-]+):$")


def read_steps(path):
    """Returns {step name: {key: first-line value, "with": {key: value}}}."""
    steps, step, key = {}, None, None
    with open(path, encoding="utf-8") as handle:
        for line in handle.read().splitlines():
            match = STEP_RE.match(line)
            if match:
                step = {"with": {}}
                steps[match.group(1)] = step
                continue
            if step is None or line.strip().startswith("#"):
                continue
            match = KEY_RE.match(line)
            if match:
                key = match.group(1)
                if key != "with":
                    step[key] = match.group(2)
            elif key == "with" and line.startswith("        "):
                name, value = line.strip().split(":", 1)
                step["with"][name] = value.strip()
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


class AuditStepTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.steps = read_steps(ACTION)

    def test_audit_steps_call_the_scan_actions(self):
        for name, (scan, directory, condition) in AUDITS.items():
            step = self.steps[name]
            with self.subTest(step=name):
                self.assertEqual(step["uses"], "$/" + scan)
                self.assertEqual(step.get("if", ""), condition)
                self.assertEqual(step["with"]["working-directory"], directory)
                self.assertEqual(step["with"]["blocking-severities"], "${{ inputs.audit-blocking-severities }}")
                self.assertEqual(step["with"]["dry-run"], "${{ inputs.dry-run }}")
                scan_inputs = action_inputs(os.path.join(ROOT, scan, "action.yml"))
                self.assertLessEqual(set(step["with"]), set(scan_inputs))

    def test_default_blocks_critical_and_no_input_turns_the_audit_off(self):
        inputs = action_inputs(ACTION)
        self.assertIn("audit-blocking-severities", inputs)
        self.assertFalse([name for name in inputs if name.startswith("audit") and name != "audit-blocking-severities"])
        with open(ACTION, encoding="utf-8") as handle:
            self.assertIn("  audit-blocking-severities:\n", handle.read())
        with open(WORKFLOW, encoding="utf-8") as handle:
            workflow = handle.read()
        variable = os.path.basename(COMPONENT).upper().replace("-", "_") + "_AUDIT_BLOCKING_SEVERITIES"
        self.assertIn("default: ${{ vars." + variable + " || 'critical' }}", workflow)
        self.assertIn("audit-blocking-severities: ${{ inputs.audit-blocking-severities }}", workflow)


if __name__ == "__main__":
    unittest.main()
