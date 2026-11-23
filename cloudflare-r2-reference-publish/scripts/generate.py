#!/usr/bin/env python3
"""Generates the reference (kind xref) of a project as a documentation package.

Subcommands, run in this order by action.yml:

  visibility  fails unless the calling repository is public: this variant
            publishes to a public bucket, so a private or internal
            repository must use the private variant of the component.
  plan      validates the inputs, resolves the version and writes the step
            outputs (version, package path, xref path); writes nothing else.
  build     runs the generator: phpDocumentor from its pinned container image,
            or TypeDoc from the project's own lockfile (npm ci, then
            npx --no-install typedoc).
  manifest  checks the generated tree and writes manifest.json at the root
            of the package.

The package is the layout that cloudflare-r2-docs-publish reads:

  <output-path>/manifest.json
  <output-path>/xref/<version>/...

Standard library only; run as python3 -I.
"""

import argparse
import json
import os
import re
import subprocess
import sys

GENERATORS = ("phpdocumentor", "typedoc")
# phpDocumentor 3.10.0, multi-architecture index (linux/amd64, linux/arm64).
PHPDOC_IMAGE = (
    "phpdoc/phpdoc:3.10.0"
    "@sha256:312ebf61ed88a6ea79aac768e43c9e9af0dd5bf3e710ed6d40ab8e53bfeeb121"
)
PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
# A release tag: "1.2.3", "v1.2.3" or "<component>-v1.2.3" (release-please).
TAG_VERSION_RE = re.compile(r"^(?:[A-Za-z0-9._-]*-)?v?([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.]+)?)$")
PATH_PART_RE = re.compile(r"^[A-Za-z0-9._@+-]+$")
NPM_LOCKFILE = "package-lock.json"
XREF_KIND = "xref"
MANIFEST = "manifest.json"
ENTRY_PAGE = "index.html"


class InputError(ValueError):
    """An input that fails validation; the message names the input."""


def split_list(value):
    """Splits a list given with commas or new lines; drops empty items."""
    return [item.strip() for item in re.split(r"[,\n]", value or "") if item.strip()]


def validate_relative_path(name, value, allow_empty=False):
    """A path relative to the workspace: no absolute path, no "..", no odd characters."""
    value = (value or "").strip()
    if not value:
        if allow_empty:
            return ""
        raise InputError(f"{name} is empty")
    if value.startswith("/") or "\\" in value:
        raise InputError(f"{name} must be a relative path with forward slashes: {value!r}")
    parts = [part for part in value.split("/") if part not in ("", ".")]
    for part in parts:
        if part == ".." or not PATH_PART_RE.match(part):
            raise InputError(f"{name} has an invalid segment {part!r}: {value!r}")
    return "/".join(parts) or "."


def validate_generator(value):
    value = (value or "").strip()
    if value not in GENERATORS:
        raise InputError(f"generator must be one of {', '.join(GENERATORS)}: {value!r}")
    return value


def validate_package_name(value):
    value = (value or "").strip()
    if not PACKAGE_NAME_RE.match(value):
        raise InputError(f"package-name must match {PACKAGE_NAME_RE.pattern}: {value!r}")
    return value


def resolve_version(version, ref_name):
    """The explicit version wins; otherwise the version comes from the tag name.

    Both forms are normalized to <MAJOR>.<MINOR>.<PATCH>[-<pre>], without a
    "v" prefix, the version form that cloudflare-r2-docs-publish publishes
    (for example 1.4.0): "1.4.0", "v1.4.0" and "<component>-v1.4.0" all give
    1.4.0.
    """
    for label, value in (("version", version), ("ref", ref_name)):
        value = (value or "").strip()
        if not value:
            continue
        match = TAG_VERSION_RE.match(value)
        if not match:
            raise InputError(
                f"{label} {value!r} holds no release version: pass a tag such as v1.2.3 "
                "or <component>-v1.2.3, or set the input version"
            )
        return match.group(1)
    raise InputError("version is empty and the run is not on a tag: set the input version or ref")


def check_visibility(visibility, private):
    """Accepts only a public repository; anything else, empty included, fails."""
    visibility = (visibility or "").strip().lower()
    private = (private or "").strip().lower()
    if visibility != "public" or private != "false":
        raise InputError(
            f"the calling repository is not public (visibility {visibility or 'unknown'!r}, "
            f"private {private or 'unknown'!r}): this component publishes to a public bucket; "
            "a private or internal repository must use the private variant"
        )


def plan(args):
    """Validates every input; returns the step outputs."""
    generator = validate_generator(args.generator)
    package_name = validate_package_name(args.package_name)
    version = resolve_version(args.version, args.ref_name)
    working_directory = validate_relative_path("working-directory", args.working_directory)
    output_path = validate_relative_path("output-path", args.output_path)
    config = validate_relative_path("config", args.config, allow_empty=True)
    sources = [validate_relative_path("source-paths", item) for item in split_list(args.source_paths)]
    workspace = os.path.realpath(args.workspace)
    project = os.path.join(workspace, working_directory)
    if not os.path.isdir(project):
        raise InputError(f"working-directory does not exist: {working_directory}")
    for source in sources:
        if not os.path.exists(os.path.join(project, source)):
            raise InputError(f"source path does not exist under working-directory: {source}")
    if config and not os.path.isfile(os.path.join(project, config)):
        raise InputError(f"config does not exist under working-directory: {config}")
    if generator == "typedoc" and not os.path.isfile(os.path.join(project, NPM_LOCKFILE)):
        raise InputError(
            f"typedoc needs {NPM_LOCKFILE} in working-directory: the generator runs from the project's lockfile"
        )
    package = os.path.join(workspace, output_path)
    if os.path.lexists(package) and (not os.path.isdir(package) or os.listdir(package)):
        raise InputError(f"output-path must be absent or an empty directory: {output_path}")
    return {
        "generator": generator,
        "package-name": package_name,
        "version": version,
        "working-directory": working_directory,
        "package-path": output_path,
        "xref-path": f"{output_path}/{XREF_KIND}/{version}",
        "config": config,
        "source-paths": "\n".join(sources),
    }


def phpdocumentor_command(workspace, working_directory, xref_path, config, sources, uid, gid):
    """docker run of the pinned phpDocumentor image; the workspace is mounted at /data."""
    command = [
        "docker", "run", "--rm", "--network=none",
        f"--user={uid}:{gid}",
        f"--volume={workspace}:/data",
        "--workdir=/data" if working_directory == "." else f"--workdir=/data/{working_directory}",
        PHPDOC_IMAGE,
        "run",
        f"--target=/data/{xref_path}",
        "--cache-folder=/tmp/phpdoc-cache",
    ]
    if config:
        command.append(f"--config={config}")
    command.extend(f"--directory={source}" for source in sources)
    return command


def typedoc_commands(workspace, xref_path, config, sources):
    """npm ci from the lockfile, then the TypeDoc the lockfile installed."""
    install = ["npm", "ci", "--no-audit", "--no-fund"]
    # TypeDoc reads "--name value", not "--name=value".
    generate = ["npx", "--no-install", "typedoc", "--out", os.path.join(workspace, xref_path)]
    if config:
        generate.extend(["--options", config])
    generate.extend(sources)
    return [install, generate]


def build(args):
    """Runs the generator; returns its exit status."""
    workspace = os.path.realpath(args.workspace)
    sources = split_list(args.source_paths)
    project = os.path.join(workspace, args.working_directory)
    os.makedirs(os.path.join(workspace, args.xref_path), exist_ok=True)
    if args.generator == "phpdocumentor":
        commands = [phpdocumentor_command(
            workspace, args.working_directory, args.xref_path, args.config, sources, os.getuid(), os.getgid()
        )]
    else:
        commands = typedoc_commands(workspace, args.xref_path, args.config, sources)
    for command in commands:
        print("+ " + " ".join(command), flush=True)
        result = subprocess.run(command, cwd=project, check=False)
        if result.returncode != 0:
            print(f"::error::{command[0]} {command[1]} failed with exit status {result.returncode}", flush=True)
            return result.returncode
    return 0


def write_manifest(args):
    """Checks the generated tree, then writes manifest.json; returns the file count."""
    workspace = os.path.realpath(args.workspace)
    xref = os.path.join(workspace, args.xref_path)
    if not os.path.isfile(os.path.join(xref, ENTRY_PAGE)):
        raise InputError(f"the generator wrote no {ENTRY_PAGE} under {args.xref_path}")
    count = sum(len(files) for _root, _dirs, files in os.walk(xref))
    manifest = {
        "package": args.package_name,
        "latest": args.version,
        "versions": {args.version: {"has_reference": True}},
    }
    path = os.path.join(workspace, args.package_path, MANIFEST)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")
    return count


def write_outputs(outputs):
    """Appends name=value lines to $GITHUB_OUTPUT, or prints them."""
    target = os.environ.get("GITHUB_OUTPUT")
    lines = []
    for name, value in outputs.items():
        if "\n" in value:
            lines.append(f"{name}<<EOF_OUTPUT\n{value}\nEOF_OUTPUT")
        else:
            lines.append(f"{name}={value}")
    text = "\n".join(lines) + "\n"
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", default=os.environ.get("GITHUB_WORKSPACE", "."))

    visibility_parser = sub.add_parser("visibility")
    visibility_parser.add_argument("--visibility", default="")
    visibility_parser.add_argument("--private", default="")

    plan_parser = sub.add_parser("plan", parents=[common])
    plan_parser.add_argument("--generator", required=True)
    plan_parser.add_argument("--package-name", required=True)
    plan_parser.add_argument("--version", default="")
    plan_parser.add_argument("--ref-name", default="")
    plan_parser.add_argument("--working-directory", default=".")
    plan_parser.add_argument("--output-path", default=".docs-dist")
    plan_parser.add_argument("--config", default="")
    plan_parser.add_argument("--source-paths", default="")

    build_parser = sub.add_parser("build", parents=[common])
    build_parser.add_argument("--generator", required=True)
    build_parser.add_argument("--working-directory", required=True)
    build_parser.add_argument("--xref-path", required=True)
    build_parser.add_argument("--config", default="")
    build_parser.add_argument("--source-paths", default="")

    manifest_parser = sub.add_parser("manifest", parents=[common])
    manifest_parser.add_argument("--package-name", required=True)
    manifest_parser.add_argument("--version", required=True)
    manifest_parser.add_argument("--package-path", required=True)
    manifest_parser.add_argument("--xref-path", required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "visibility":
            check_visibility(args.visibility, args.private)
            return 0
        if args.command == "plan":
            write_outputs(plan(args))
            return 0
        if args.command == "build":
            return build(args)
        count = write_manifest(args)
        write_outputs({"files": str(count)})
        return 0
    except InputError as error:
        print(f"::error::{error}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
