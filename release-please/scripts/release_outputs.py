"""Turns the outputs of release-please-action into fixed-name outputs.

release-please-action names the outputs of a package that is not the root
after its path (<path>--tag_name, <path>--version), and a composite action
cannot pass dynamic output names through. This script reads every output of
the release-please step as JSON and writes:

- releases: the releases of this run, as a JSON array of
  {"path", "tag-name", "version"} objects in the order of paths_released;
  [] when none. The root package has the path ".".
- release-pr-created: true when this run created or updated a release pull
  request, false otherwise.
- release-pr-branch / release-pr-number: head branch and number of the
  first release pull request of this run; empty when none.
- release-prs: every release pull request of this run, as a JSON array of
  {"branch", "number"} objects; [] when none.

Environment:
  RELEASE_OUTPUTS  JSON object of the outputs of release-please-action
                   (toJSON(steps.<id>.outputs)).
  GITHUB_OUTPUT    file that receives the outputs.

Standard library only. Run: python3 -I release_outputs.py
"""

import json
import os
import sys


class OutputsError(Exception):
    """The outputs of release-please-action are not what this script reads."""


def parse_json(name, value, expected_type, default):
    """Returns the JSON value of output name, or default when it is empty."""
    if value in (None, ""):
        return default
    try:
        data = json.loads(value)
    except json.JSONDecodeError as error:
        raise OutputsError(f"output {name} of release-please-action is not valid JSON ({error})") from error
    if not isinstance(data, expected_type):
        raise OutputsError(f"output {name} of release-please-action is not a JSON {expected_type.__name__}")
    return data


def output_key(path, key):
    """Name of the output key of the package at path (unprefixed for the root)."""
    return key if path == "." else f"{path}--{key}"


def releases(outputs):
    """The releases of this run, in the order of paths_released."""
    paths = parse_json("paths_released", outputs.get("paths_released"), list, [])
    result = []
    for path in paths:
        if not isinstance(path, str) or not path:
            raise OutputsError("output paths_released of release-please-action holds a value that is not a path")
        result.append(
            {
                "path": path,
                "tag-name": str(outputs.get(output_key(path, "tag_name"), "")),
                "version": str(outputs.get(output_key(path, "version"), "")),
            }
        )
    return result


def release_prs(outputs):
    """The release pull requests that this run created or updated."""
    if str(outputs.get("prs_created", "false")).lower() != "true":
        return []
    prs = parse_json("prs", outputs.get("prs"), list, [])
    result = []
    for pr in prs:
        if not isinstance(pr, dict):
            raise OutputsError("output prs of release-please-action holds a value that is not a JSON object")
        number = pr.get("number")
        branch = pr.get("headBranchName")
        if not isinstance(number, int) or not isinstance(branch, str) or not branch:
            raise OutputsError("output prs of release-please-action has a pull request without number or headBranchName")
        result.append({"branch": branch, "number": number})
    return result


def build_outputs(outputs):
    """Returns the fixed-name outputs of the component."""
    prs = release_prs(outputs)
    first = prs[0] if prs else {}
    return {
        "releases": json.dumps(releases(outputs), separators=(",", ":")),
        "release-pr-created": "true" if prs else "false",
        "release-pr-branch": first.get("branch", ""),
        "release-pr-number": str(first.get("number", "")),
        "release-prs": json.dumps(prs, separators=(",", ":")),
    }


def write_outputs(outputs):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


def main():
    try:
        raw = parse_json("(all)", os.environ.get("RELEASE_OUTPUTS", ""), dict, {})
        outputs = build_outputs(raw)
    except OutputsError as error:
        print(f"::error::{error}", flush=True)
        return 1
    for key, value in outputs.items():
        print(f"{key}={value}")
    write_outputs(outputs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
