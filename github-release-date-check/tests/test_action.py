"""Tests of the wiring in action.yml.

Checks that every input reaches the script only through env: (never inside
run:), and that every env: value comes from an input of the action.

The files are read as text, standard library only.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ACTION = os.path.join(COMPONENT, "action.yml")
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


if __name__ == "__main__":
    unittest.main()
