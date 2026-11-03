"""Compares the outputs of the dry run with an expected plan.

Usage: COMPONENT=... VERSION=... COMMANDS=<JSON array> compare_plan.py <expected.json>
Run as ``python3 -I -B``; standard library only.
"""

import json
import os
import sys


def main(expected_file: str) -> int:
    with open(expected_file, encoding="utf-8") as handle:
        expected = json.load(handle)
    try:
        commands = json.loads(os.environ.get("COMMANDS", ""))
    except json.JSONDecodeError:
        print("::error::the dry-run output commands is not a JSON array", flush=True)
        return 1
    actual = {
        "component": os.environ.get("COMPONENT", ""),
        "version": os.environ.get("VERSION", ""),
        "commands": commands,
    }
    print(json.dumps(actual, indent=2))
    if actual != expected:
        print(f"::error::dry-run outputs differ from {expected_file}: expected {json.dumps(expected)}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: compare_plan.py <expected.json>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
