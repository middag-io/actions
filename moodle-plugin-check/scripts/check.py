"""Checks a Moodle plugin and runs its Composer checks.

Two subcommands, both called by action.yml:

  plan  validates the inputs and the plugin metadata (version.php and the
        English language file), then writes the plan: the Composer commands
        to run, as argument lists. Writes nothing else; a dry run stops here.
  run   runs the commands of a plan file, in order, without a shell, and
        stops at the first failure.

Standard library only. Run as ``python3 -I``.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

# Frankenstyle component name: <type>_<name> (Moodle developer documentation,
# "Frankenstyle").
COMPONENT_RE = re.compile(r"^[a-z][a-z0-9]*_[a-z][a-z0-9_]*$")
# YYYYMMDDXX: the date of the change and a two-digit increment.
VERSION_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\.\d+)?$")
MATURITIES = ("MATURITY_ALPHA", "MATURITY_BETA", "MATURITY_RC", "MATURITY_STABLE")
PHP_VERSION_RE = re.compile(r"^\d+\.\d+$")
PHP_EXTENSIONS_RE = re.compile(r"^[A-Za-z0-9_:, -]*$")
COMPOSER_OPTION_RE = re.compile(r"^--[a-z][a-z0-9-]*(=[A-Za-z0-9._/,:-]+)?$")
COMPOSER_SCRIPT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.-]*$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
# The fields of version.php that the check reads. Other fields, such as
# $plugin->supported or $plugin->dependencies, may hold arrays or any other
# expression and are left alone.
READ_FIELDS = ("component", "version", "requires", "maturity", "release")
# The plugin type whose language file is lang/en/<name>.php, not
# lang/en/<component>.php.
MODULE_TYPE = "mod"

ASSIGNMENT_RE = re.compile(
    r"^\s*\$plugin->(?P<field>[a-z_]+)\s*=\s*(?P<value>.+?)\s*;",
    re.MULTILINE,
)


class CheckError(Exception):
    """A finding that stops the check; the message names the input or file."""


@dataclass(frozen=True)
class PluginInfo:
    component: str
    version: str
    release: str
    maturity: str


@dataclass(frozen=True)
class Options:
    path: str
    component: str
    php_version: str
    php_extensions: str
    composer_validate: bool
    composer_options: tuple[str, ...]
    composer_scripts: tuple[str, ...]


def parse_bool(name: str, value: str) -> bool:
    if value in ("true", "false"):
        return value == "true"
    raise CheckError(f"input {name} must be true or false, got {value!r}")


def split_list(value: str) -> tuple[str, ...]:
    """Splits a list separated by commas or new lines; drops empty items."""
    items = re.split(r"[,\n]", value)
    return tuple(item.strip() for item in items if item.strip())


def parse_options(args: argparse.Namespace) -> Options:
    if not PHP_VERSION_RE.match(args.php_version):
        raise CheckError(f"input php-version must look like 8.3, got {args.php_version!r}")
    if not PHP_EXTENSIONS_RE.match(args.php_extensions):
        raise CheckError("input php-extensions holds characters other than letters, digits, _ : , - and spaces")
    if args.component and not COMPONENT_RE.match(args.component):
        raise CheckError(f"input component must be a frankenstyle name such as local_example, got {args.component!r}")
    composer_options = tuple(args.composer_options.split())
    for option in composer_options:
        if not COMPOSER_OPTION_RE.match(option):
            raise CheckError(f"input composer-options: {option!r} is not a long option such as --prefer-dist")
    composer_scripts = split_list(args.composer_scripts)
    for script in composer_scripts:
        if not COMPOSER_SCRIPT_RE.match(script):
            raise CheckError(f"input composer-scripts: {script!r} is not a Composer script name")
    return Options(
        path=args.path,
        component=args.component,
        php_version=args.php_version,
        php_extensions=args.php_extensions,
        composer_validate=parse_bool("composer-validate", args.composer_validate),
        composer_options=composer_options,
        composer_scripts=composer_scripts,
    )


def resolve_plugin_dir(workspace: Path, path: str) -> Path:
    """Resolves path inside the workspace; refuses a path that leaves it."""
    if not path or Path(path).is_absolute():
        raise CheckError(f"input path must be a relative directory, got {path!r}")
    root = workspace.resolve()
    plugin_dir = (root / path).resolve()
    if plugin_dir != root and root not in plugin_dir.parents:
        raise CheckError(f"input path {path!r} leaves the checkout")
    if not plugin_dir.is_dir():
        raise CheckError(f"input path {path!r} is not a directory of the checkout")
    return plugin_dir


def php_literal(field: str, raw: str) -> str:
    """Reads a PHP string, integer, float or constant literal."""
    quoted = re.fullmatch(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\$]|\\.)*)\"", raw)
    if quoted:
        text = quoted.group(1) if quoted.group(1) is not None else quoted.group(2)
        return re.sub(r"\\(.)", r"\1", text)
    if re.fullmatch(r"[0-9]+(\.[0-9]+)?|[A-Z_]+", raw):
        return raw
    raise CheckError(f"version.php: $plugin->{field} is not a literal value")


def strip_php_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*(//|#).*$", "", source)


def read_version_php(plugin_dir: Path) -> dict[str, str]:
    version_file = plugin_dir / "version.php"
    if not version_file.is_file():
        raise CheckError("version.php is missing at the root of the plugin (input path)")
    source = strip_php_comments(version_file.read_text(encoding="utf-8"))
    if not source.lstrip().startswith("<?php"):
        raise CheckError("version.php does not start with <?php")
    fields: dict[str, str] = {}
    for match in ASSIGNMENT_RE.finditer(source):
        field = match.group("field")
        if field not in READ_FIELDS:
            continue
        if field in fields:
            raise CheckError(f"version.php sets $plugin->{field} more than once")
        fields[field] = php_literal(field, match.group("value"))
    return fields


def check_version(version: str) -> None:
    match = VERSION_RE.match(version)
    if not match:
        raise CheckError(f"version.php: $plugin->version must be YYYYMMDDXX, got {version!r}")
    year, month, day = (int(match.group(i)) for i in (1, 2, 3))
    try:
        datetime.date(year, month, day)
    except ValueError as error:
        raise CheckError(f"version.php: $plugin->version {version} holds no valid date") from error


def language_file(component: str) -> str:
    plugin_type, name = component.split("_", 1)
    return f"lang/en/{name if plugin_type == MODULE_TYPE else component}.php"


def check_plugin(plugin_dir: Path, expected_component: str) -> PluginInfo:
    fields = read_version_php(plugin_dir)
    for required in ("component", "version"):
        if required not in fields:
            raise CheckError(f"version.php does not set $plugin->{required}")
    component = fields["component"]
    if not COMPONENT_RE.match(component):
        raise CheckError(f"version.php: $plugin->component is not a frankenstyle name, got {component!r}")
    if expected_component and component != expected_component:
        raise CheckError(f"version.php: $plugin->component is {component}, the input component is {expected_component}")
    check_version(fields["version"])
    if "requires" in fields:
        check_version(fields["requires"])
    maturity = fields.get("maturity", "")
    if maturity and maturity not in MATURITIES:
        raise CheckError(f"version.php: $plugin->maturity must be one of {', '.join(MATURITIES)}, got {maturity!r}")
    lang = language_file(component)
    if not (plugin_dir / lang).is_file():
        raise CheckError(f"{lang} is missing: every plugin has an English language file")
    return PluginInfo(
        component=component,
        version=fields["version"],
        release=fields.get("release", ""),
        maturity=maturity,
    )


def build_plan(plugin_dir: Path, options: Options) -> list[list[str]]:
    has_composer = (plugin_dir / "composer.json").is_file()
    if not has_composer:
        if options.composer_scripts:
            raise CheckError("input composer-scripts is set but the plugin has no composer.json")
        return []
    plan: list[list[str]] = []
    if options.composer_validate:
        plan.append(["composer", "validate", "--no-interaction"])
    plan.append(["composer", "install", "--no-interaction", *options.composer_options])
    plan.extend(["composer", "run-script", script] for script in options.composer_scripts)
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
    has_key = parse_bool("has-app-private-key", args.has_app_private_key)
    dry_run = parse_bool("dry-run", args.dry_run)
    repositories = split_list(args.app_repositories)
    for repository in repositories:
        if not REPOSITORY_RE.match(repository):
            raise CheckError(f"input app-repositories: {repository!r} is not a repository name without owner")
    if args.app_client_id and not repositories:
        raise CheckError(
            "input app-client-id is set but app-repositories is empty: name the repositories of the private "
            "dependencies (variable MOODLE_PLUGIN_CHECK_APP_REPOSITORIES), so that the token does not reach every "
            "repository of the App installation"
        )
    if args.app_client_id and not has_key and not dry_run:
        raise CheckError(
            "input app-client-id is set but app-private-key is empty: pass the secret "
            "MOODLE_PLUGIN_CHECK_APP_PRIVATE_KEY, or leave app-client-id empty for public dependencies"
        )
    plugin_dir = resolve_plugin_dir(Path(args.workspace), options.path)
    info = check_plugin(plugin_dir, options.component)
    plan = build_plan(plugin_dir, options)
    plan_file = Path(args.plan_file)
    plan_file.write_text(json.dumps({"directory": str(plugin_dir), "commands": plan}), encoding="utf-8")
    commands = json.dumps([" ".join(command) for command in plan])
    print(f"Plugin {info.component} version {info.version} (PHP {options.php_version}).")
    for command in plan:
        print(("Would run: " if dry_run else "Will run: ") + " ".join(command))
    if not plan:
        print("No composer.json: only the plugin metadata is checked.")
    write_outputs(
        args.github_output,
        {
            "component": info.component,
            "version": info.version,
            "release": info.release,
            "maturity": info.maturity,
            "commands": commands,
            "plan-file": str(plan_file),
        },
    )
    return 0


def composer_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Copies the environment; adds COMPOSER_AUTH when a token is present."""
    env = {key: value for key, value in environ.items() if key != "COMPOSER_GITHUB_TOKEN"}
    token = environ.get("COMPOSER_GITHUB_TOKEN", "")
    if token:
        env["COMPOSER_AUTH"] = json.dumps({"github-oauth": {"github.com": token}})
    return env


def command_run(args: argparse.Namespace, runner=subprocess.run) -> int:
    plan = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
    env = composer_env(os.environ)
    for command in plan["commands"]:
        print(f"::group::{' '.join(command)}", flush=True)
        result = runner(command, cwd=plan["directory"], env=env, check=False)
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
    plan.add_argument("--component", default="")
    plan.add_argument("--php-version", required=True)
    plan.add_argument("--php-extensions", default="")
    plan.add_argument("--composer-validate", default="true")
    plan.add_argument("--composer-options", default="")
    plan.add_argument("--composer-scripts", default="")
    plan.add_argument("--app-client-id", default="")
    plan.add_argument("--app-repositories", default="")
    plan.add_argument("--has-app-private-key", default="false")
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
