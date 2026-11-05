"""Plans and runs the checks of a PHP library (a Composer package).

Two subcommands, both called by action.yml:

  plan  validates the inputs and writes the plan: the Composer commands to
        run, as argument lists. Writes nothing else; a dry run stops here.
  run   runs the commands of a plan file, in order, without a shell, and
        stops at the first failure.

The static checks (composer validate, the PSR-4 autoload check and the
lint, PHPStan, PHP-CS-Fixer and Rector scripts) run only in the job whose
static-checks input is true; the test script runs in every job.

Standard library only. Run as ``python3 -I``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

PHP_VERSION_RE = re.compile(r"\d+\.\d+")
COMPOSER_SCRIPT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.-]*")
# Inputs that name one Composer script each, in the order they run.
STATIC_SCRIPTS = ("lint-script", "phpstan-script", "cs-fixer-script", "rector-script")


class CheckError(Exception):
    """A finding that stops the check; the message names the input or file."""


@dataclass(frozen=True)
class Options:
    path: str
    php_version: str
    static_checks: bool
    composer_validate: bool
    composer_validate_strict: bool
    strict_psr: bool
    static_scripts: tuple[str, ...]
    composer_scripts: tuple[str, ...]
    test_script: str


def parse_bool(name: str, value: str) -> bool:
    if value in ("true", "false"):
        return value == "true"
    raise CheckError(f"input {name} must be true or false, got {value!r}")


def split_list(value: str) -> tuple[str, ...]:
    """Splits a list separated by commas or new lines; drops empty items."""
    return tuple(item.strip() for item in re.split(r"[,\n]", value) if item.strip())


def check_script(name: str, value: str) -> str:
    if value and not COMPOSER_SCRIPT_RE.fullmatch(value):
        raise CheckError(f"input {name}: {value!r} is not a Composer script name")
    return value


def parse_options(args: argparse.Namespace) -> Options:
    if not PHP_VERSION_RE.fullmatch(args.php_version):
        raise CheckError(f"input php-version must look like 8.3, got {args.php_version!r}")
    scripts = {name: check_script(name, getattr(args, name.replace("-", "_"))) for name in STATIC_SCRIPTS}
    extra = tuple(check_script("composer-scripts", script) for script in split_list(args.composer_scripts))
    return Options(
        path=args.path,
        php_version=args.php_version,
        static_checks=parse_bool("static-checks", args.static_checks),
        composer_validate=parse_bool("composer-validate", args.composer_validate),
        composer_validate_strict=parse_bool("composer-validate-strict", args.composer_validate_strict),
        strict_psr=parse_bool("strict-psr", args.strict_psr),
        static_scripts=tuple(script for script in scripts.values() if script),
        composer_scripts=extra,
        test_script=check_script("test-script", args.test_script),
    )


def resolve_library_dir(workspace: Path, path: str) -> Path:
    """Resolves path inside the workspace; refuses a path that leaves it."""
    if not path or Path(path).is_absolute():
        raise CheckError(f"input path must be a relative directory, got {path!r}")
    root = workspace.resolve()
    library_dir = (root / path).resolve()
    if library_dir != root and root not in library_dir.parents:
        raise CheckError(f"input path {path!r} leaves the checkout")
    if not (library_dir / "composer.json").is_file():
        raise CheckError(f"input path {path!r} has no composer.json")
    return library_dir


def static_plan(options: Options) -> list[list[str]]:
    plan: list[list[str]] = []
    if options.composer_validate:
        validate = ["composer", "validate", "--no-interaction", "--no-check-publish", "--no-check-lock"]
        plan.append([*validate, "--strict"] if options.composer_validate_strict else validate)
    if options.strict_psr:
        plan.append(["composer", "dump-autoload", "--no-interaction", "--optimize", "--strict-psr"])
    scripts = (*options.static_scripts, *options.composer_scripts)
    plan.extend(["composer", "run-script", "--no-interaction", script] for script in scripts)
    return plan


def build_plan(options: Options) -> list[list[str]]:
    plan = static_plan(options) if options.static_checks else []
    if options.test_script:
        plan.append(["composer", "run-script", "--no-interaction", options.test_script])
    return plan


def write_outputs(path: str, values: Mapping[str, str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def command_plan(args: argparse.Namespace) -> int:
    options = parse_options(args)
    dry_run = parse_bool("dry-run", args.dry_run)
    library_dir = resolve_library_dir(Path(args.workspace), options.path)
    plan = build_plan(options)
    plan_file = Path(args.plan_file)
    plan_file.write_text(json.dumps({"directory": str(library_dir), "commands": plan}), encoding="utf-8")
    kind = "static checks and tests" if options.static_checks else "tests"
    print(f"PHP {options.php_version}: {kind}.")
    for command in plan:
        print(("Would run: " if dry_run else "Will run: ") + " ".join(command))
    write_outputs(args.github_output, {
        "commands": json.dumps([" ".join(command) for command in plan]),
        "plan-file": str(plan_file),
    })
    return 0


def command_run(args: argparse.Namespace) -> int:
    plan = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
    # The Composer credentials are in the global Composer auth.json, written
    # by php-composer-setup before this step.
    env = dict(os.environ)
    for command in plan["commands"]:
        print(f"::group::{' '.join(command)}", flush=True)
        result = subprocess.run(command, cwd=plan["directory"], env=env, check=False)
        print("::endgroup::", flush=True)
        if result.returncode != 0:
            raise CheckError(f"{' '.join(command)} failed with exit code {result.returncode}")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--workspace", required=True)
    plan.add_argument("--path", default=".")
    plan.add_argument("--php-version", required=True)
    plan.add_argument("--static-checks", default="true")
    plan.add_argument("--composer-validate", default="true")
    plan.add_argument("--composer-validate-strict", default="false")
    plan.add_argument("--strict-psr", default="true")
    for name in STATIC_SCRIPTS:
        plan.add_argument(f"--{name}", default="")
    plan.add_argument("--composer-scripts", default="")
    plan.add_argument("--test-script", default="")
    plan.add_argument("--dry-run", default="false")
    plan.add_argument("--plan-file", required=True)
    plan.add_argument("--github-output", default="")
    run = sub.add_parser("run")
    run.add_argument("--plan-file", required=True)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return command_plan(args) if args.command == "plan" else command_run(args)
    except CheckError as error:
        print(f"::error::{error}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
