"""Checks the inputs of the release-please component before any write.

Reads the release-please configuration and manifest of the calling
repository (checked out in the workspace), validates them and the other
inputs, and writes the package paths of the configuration to the step
outputs. Outside a dry run it also requires the GitHub App credentials.

Exit status: 0 when every check passes, 1 otherwise. Every problem is
printed as a GitHub error annotation that names the input and, where one
exists, the variable or secret that fills it.

Standard library only. Run: python3 -I check_config.py --help
"""

import argparse
import json
import os
import re
import sys

# A repository-relative path: no absolute path, no "..", no empty segment.
PATH_RE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/-]+$")
# A git branch name, as far as this component accepts one.
BRANCH_RE = re.compile(r"^(?!/)(?!.*\.\.)(?!.*//)[A-Za-z0-9._/-]+(?<!/)$")
# SemVer 2.0.0 (https://semver.org), the version format of the manifest.
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


class CheckError(Exception):
    """A problem in the inputs or in the files they point to."""


def parse_bool(name, value):
    """Returns the boolean of a "true"/"false" input."""
    if value in ("true", "false"):
        return value == "true"
    raise CheckError(f"input {name} must be true or false, got {value!r}")


def check_path(name, value):
    """Rejects a path that is empty, absolute or leaves the workspace."""
    if not PATH_RE.match(value):
        raise CheckError(f"input {name} must be a path relative to the repository root, got {value!r}")
    return value


def read_json_object(name, path):
    """Returns the JSON object stored at path."""
    if not os.path.isfile(path):
        raise CheckError(f"input {name}: file {path} not found in the repository")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CheckError(f"input {name}: {path} is not valid JSON ({error})") from error
    if not isinstance(data, dict):
        raise CheckError(f"input {name}: {path} must hold a JSON object")
    return data


def check_config(path):
    """Returns the sorted package paths of a release-please configuration."""
    config = read_json_object("config-file", path)
    packages = config.get("packages")
    if not isinstance(packages, dict) or not packages:
        raise CheckError(f"input config-file: {path} has no packages object with at least one package")
    for package, options in packages.items():
        if package != "." and not PATH_RE.match(package):
            raise CheckError(f"input config-file: package path {package!r} in {path} is not a relative path")
        if not isinstance(options, dict):
            raise CheckError(f"input config-file: package {package!r} in {path} must map to an object")
    return sorted(packages)


def check_manifest(path, packages):
    """Validates the versions of a release-please manifest.

    A configured package may be missing from the manifest: release-please
    adds it with its first release. A manifest entry must be a configured
    package with a SemVer version.
    """
    manifest = read_json_object("manifest-file", path)
    for package, version in manifest.items():
        if package not in packages:
            raise CheckError(f"input manifest-file: package {package!r} in {path} is not in the configuration")
        if not isinstance(version, str) or not SEMVER_RE.match(version):
            raise CheckError(f"input manifest-file: version of {package!r} in {path} is not SemVer, got {version!r}")


def check_credentials(dry_run, has_client_id, has_private_key):
    """Outside a dry run, both GitHub App credentials are required."""
    if dry_run:
        return
    if not has_client_id:
        raise CheckError(
            "input app-client-id is empty: pass it in with: or set the variable RELEASE_PLEASE_APP_CLIENT_ID"
            " (or set dry-run: true)"
        )
    if not has_private_key:
        raise CheckError(
            "secret app-private-key is empty: pass it with one line in the caller's secrets block"
            " (from the secret RELEASE_PLEASE_APP_PRIVATE_KEY) or set dry-run: true"
        )


def write_outputs(path, outputs):
    """Appends name=value lines to the GITHUB_OUTPUT file."""
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for name, value in outputs.items():
            handle.write(f"{name}={value}\n")


def run(args):
    """Runs every check; returns the outputs."""
    dry_run = parse_bool("dry-run", args.dry_run)
    has_client_id = parse_bool("has-app-client-id", args.has_app_client_id)
    has_private_key = parse_bool("has-app-private-key", args.has_app_private_key)
    config_file = check_path("config-file", args.config_file)
    manifest_file = check_path("manifest-file", args.manifest_file)
    if args.target_branch and not BRANCH_RE.match(args.target_branch):
        raise CheckError(f"input target-branch is not a valid branch name, got {args.target_branch!r}")
    check_credentials(dry_run, has_client_id, has_private_key)
    packages = check_config(config_file)
    check_manifest(manifest_file, packages)
    return {"packages": json.dumps(packages, separators=(",", ":"))}


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config-file", required=True)
    parser.add_argument("--manifest-file", required=True)
    parser.add_argument("--target-branch", default="")
    parser.add_argument("--dry-run", default="false")
    parser.add_argument("--has-app-client-id", default="false")
    parser.add_argument("--has-app-private-key", default="false")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        outputs = run(args)
    except CheckError as error:
        print(f"::error::{error}")
        return 1
    write_outputs(os.environ.get("GITHUB_OUTPUT", ""), outputs)
    mode = "dry run: would run release-please" if args.dry_run == "true" else "running release-please"
    print(f"{mode} with {args.config_file} and {args.manifest_file}; packages {outputs['packages']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
