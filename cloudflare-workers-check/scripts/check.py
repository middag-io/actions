"""Checks a Cloudflare Workers project in a change.

Two subcommands, both called by action.yml:

  plan  validates the inputs and the project (package.json, package-lock.json
        and the Wrangler configuration), then writes the plan: the commands
        to run, as argument lists. Writes nothing else; a dry run stops here.
  run   runs the commands of a plan file, in order, without a shell, and
        stops at the first failure.

Standard library only. Run as ``python3 -I``.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

# Major, major.minor or major.minor.patch (for example 22 or 22.11.0).
NODE_VERSION_RE = re.compile(r"^\d+(\.\d+){0,2}$")
# npm script names that are safe as one argument: no spaces, no shell syntax.
NPM_SCRIPT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.-]*$")
# Configuration files that Wrangler finds by itself, in its own order
# (Cloudflare Workers documentation, "Configuration").
WRANGLER_CONFIG_NAMES = ("wrangler.json", "wrangler.jsonc", "wrangler.toml")
WRANGLER_PACKAGE = "wrangler"
LOCK_FILE = "package-lock.json"


class CheckError(Exception):
    """A finding that stops the check; the message names the input or file."""


@dataclass(frozen=True)
class Options:
    path: str
    node_version: str
    npm_scripts: tuple[str, ...]
    npm_scripts_if_present: tuple[str, ...]
    wrangler_config: str
    wrangler_dry_run: bool


@dataclass(frozen=True)
class Project:
    name: str
    scripts: frozenset[str]
    dependencies: frozenset[str]


def parse_bool(name: str, value: str) -> bool:
    if value in ("true", "false"):
        return value == "true"
    raise CheckError(f"input {name} must be true or false, got {value!r}")


def split_list(value: str) -> tuple[str, ...]:
    """Splits a list separated by commas or new lines; drops empty items."""
    items = re.split(r"[,\n]", value)
    return tuple(item.strip() for item in items if item.strip())


def parse_scripts(name: str, value: str) -> tuple[str, ...]:
    scripts = split_list(value)
    for script in scripts:
        if not NPM_SCRIPT_RE.match(script):
            raise CheckError(f"input {name}: {script!r} is not an npm script name")
    return scripts


def parse_options(args: argparse.Namespace) -> Options:
    if not NODE_VERSION_RE.match(args.node_version):
        raise CheckError(f"input node-version must look like 22 or 22.11.0, got {args.node_version!r}")
    npm_scripts = parse_scripts("npm-scripts", args.npm_scripts)
    if_present = parse_scripts("npm-scripts-if-present", args.npm_scripts_if_present)
    repeated = sorted(set(npm_scripts) & set(if_present))
    if repeated:
        raise CheckError(f"script {repeated[0]!r} is in both npm-scripts and npm-scripts-if-present")
    return Options(
        path=args.path,
        node_version=args.node_version,
        npm_scripts=npm_scripts,
        npm_scripts_if_present=if_present,
        wrangler_config=args.wrangler_config,
        wrangler_dry_run=parse_bool("wrangler-dry-run", args.wrangler_dry_run),
    )


def resolve_inside(root: Path, path: str, name: str) -> Path:
    """Resolves path inside root; refuses an absolute path or one that leaves root."""
    if not path or Path(path).is_absolute():
        raise CheckError(f"input {name} must be a relative path, got {path!r}")
    base = root.resolve()
    target = (base / path).resolve()
    if target != base and base not in target.parents:
        raise CheckError(f"input {name} {path!r} leaves the checkout")
    return target


def resolve_project_dir(workspace: Path, path: str) -> Path:
    project_dir = resolve_inside(workspace, path, "path")
    if not project_dir.is_dir():
        raise CheckError(f"input path {path!r} is not a directory of the checkout")
    return project_dir


def read_project(project_dir: Path) -> Project:
    package_file = project_dir / "package.json"
    if not package_file.is_file():
        raise CheckError("package.json is missing at the root of the project (input path)")
    try:
        package = json.loads(package_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CheckError(f"package.json is not valid JSON: {error}") from error
    if not isinstance(package, dict):
        raise CheckError("package.json does not hold a JSON object")
    if not (project_dir / LOCK_FILE).is_file():
        raise CheckError(f"{LOCK_FILE} is missing next to package.json: npm ci installs from it")
    scripts = package.get("scripts", {})
    if not isinstance(scripts, dict):
        raise CheckError("package.json: scripts is not an object")
    dependencies: set[str] = set()
    for field in ("dependencies", "devDependencies"):
        section = package.get(field, {})
        if not isinstance(section, dict):
            raise CheckError(f"package.json: {field} is not an object")
        dependencies.update(section)
    name = package.get("name", "")
    return Project(
        name=name if isinstance(name, str) else "",
        scripts=frozenset(scripts),
        dependencies=frozenset(dependencies),
    )


def find_wrangler_config(project_dir: Path, wrangler_config: str) -> str:
    """Returns the Wrangler configuration, relative to the project directory."""
    if wrangler_config:
        config = resolve_inside(project_dir, wrangler_config, "wrangler-config")
        if not config.is_file():
            raise CheckError(f"input wrangler-config {wrangler_config!r} is not a file of the project")
        return config.relative_to(project_dir.resolve()).as_posix()
    for name in WRANGLER_CONFIG_NAMES:
        if (project_dir / name).is_file():
            return name
    raise CheckError(
        "no Wrangler configuration in the project: add one of "
        f"{', '.join(WRANGLER_CONFIG_NAMES)}, or set the input wrangler-config"
    )


def build_plan(project: Project, options: Options, wrangler_config: str) -> list[list[str]]:
    missing = [script for script in options.npm_scripts if script not in project.scripts]
    if missing:
        raise CheckError(f"input npm-scripts: package.json has no script {missing[0]!r}")
    plan: list[list[str]] = [["npm", "ci"]]
    plan.extend(["npm", "run", script] for script in options.npm_scripts_if_present if script in project.scripts)
    plan.extend(["npm", "run", script] for script in options.npm_scripts)
    if options.wrangler_dry_run:
        if WRANGLER_PACKAGE not in project.dependencies:
            raise CheckError(
                "input wrangler-dry-run is true but wrangler is not in the dependencies or devDependencies of "
                "package.json: the check runs the version of the lock file and never downloads one"
            )
        plan.append(["npx", "--no-install", "wrangler", "deploy", "--dry-run", "--config", wrangler_config])
    return plan


def write_outputs(path: str, values: Mapping[str, str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            if "\n" in value:
                raise CheckError(f"output {key} holds a new line")
            handle.write(f"{key}={value}\n")


def command_plan(args: argparse.Namespace) -> int:
    options = parse_options(args)
    dry_run = parse_bool("dry-run", args.dry_run)
    project_dir = resolve_project_dir(Path(args.workspace), options.path)
    project = read_project(project_dir)
    wrangler_config = find_wrangler_config(project_dir, options.wrangler_config)
    plan = build_plan(project, options, wrangler_config)
    skipped = [script for script in options.npm_scripts_if_present if script not in project.scripts]
    Path(args.plan_file).write_text(json.dumps({"directory": str(project_dir), "commands": plan}), encoding="utf-8")
    print(f"Project {project.name or '(no name)'} with Wrangler configuration {wrangler_config} (Node.js {options.node_version}).")
    for script in skipped:
        print(f"Skipped npm script {script}: not in package.json.")
    for command in plan:
        print(("Would run: " if dry_run else "Will run: ") + " ".join(command))
    write_outputs(
        args.github_output,
        {
            "name": project.name,
            "wrangler-config": wrangler_config,
            "commands": json.dumps([" ".join(command) for command in plan]),
            "plan-file": args.plan_file,
        },
    )
    return 0


def command_run(args: argparse.Namespace, runner=subprocess.run) -> int:
    plan = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
    for command in plan["commands"]:
        print(f"::group::{' '.join(command)}", flush=True)
        result = runner(command, cwd=plan["directory"], check=False)
        print("::endgroup::", flush=True)
        if result.returncode != 0:
            raise CheckError(f"{' '.join(command)} failed with exit code {result.returncode}")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--workspace", required=True)
    plan.add_argument("--path", required=True)
    plan.add_argument("--node-version", required=True)
    plan.add_argument("--npm-scripts", default="")
    plan.add_argument("--npm-scripts-if-present", default="")
    plan.add_argument("--wrangler-config", default="")
    plan.add_argument("--wrangler-dry-run", default="false")
    plan.add_argument("--dry-run", default="false")
    plan.add_argument("--plan-file", required=True)
    plan.add_argument("--github-output", default="")
    run = sub.add_parser("run")
    run.add_argument("--plan-file", required=True)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "plan":
            return command_plan(args)
        return command_run(args)
    except CheckError as error:
        # Workflow commands are read from standard output.
        print(f"::error::{error}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
