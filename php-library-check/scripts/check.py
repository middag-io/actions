"""Plans and runs the checks of a PHP library (a Composer package).

Two subcommands, both called by action.yml:

  plan  validates the inputs and writes the plan: the Composer commands to
        run, as argument lists. Writes nothing else; a dry run stops here.
  run   checks the installed major version of each package of
        expect-majors, then runs the commands of a plan file, in order,
        without a shell, and stops at the first failure.

The static checks (composer validate, the PSR-4 autoload check and the
lint, PHPStan, PHP-CS-Fixer and Rector scripts) run only in the job whose
static-checks input is true; the test script runs in every job.

With tool-cache-paths, the plan also names the cache of the static check
tools (PHPStan, Rector, PHP-CS-Fixer): the directories to restore and save
and the actions/cache key, which action.yml passes to actions/cache.

Standard library only. Run as ``python3 -I``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

PHP_VERSION_RE = re.compile(r"\d+\.\d+")
COMPOSER_SCRIPT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.-]*")
# One expect-majors item: <vendor>/<package>:<major>, a Composer package name
# (https://getcomposer.org/doc/04-schema.md#name) and a major version.
# Same language as the Composer name pattern, but each repeated group starts
# with a mandatory separator, so a run of characters splits only one way and
# matching stays linear (no catastrophic backtracking).
EXPECT_MAJOR_RE = re.compile(
    r"(?P<name>[a-z0-9]+(?:[_.-][a-z0-9]+)*/[a-z0-9]+(?:(?:[_.]|-{1,2})[a-z0-9]+)*):(?P<major>0|[1-9][0-9]*)"
)
# Major version of an installed package: "v8.1.11", "8.1.11", "8.1.x-dev".
INSTALLED_MAJOR_RE = re.compile(r"v?(0|[1-9][0-9]*)\.")
SHOW_COMMAND = ("composer", "show", "--format=json", "--no-interaction")
# Inputs that name one Composer script each, in the order they run.
STATIC_SCRIPTS = ("lint-script", "phpstan-script", "cs-fixer-script", "rector-script")
DEPENDENCY_VERSIONS = ("locked", "highest")
RUNNER_OS_RE = re.compile(r"[A-Za-z0-9]+")
COMMIT_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
# A tool-cache-paths item is a plain relative path: no glob, negation, home,
# variable or backslash, which actions/cache would expand or misread.
UNSAFE_CACHE_PATH_RE = re.compile(r"[*?\[\]{}!~$\\]")
TOOL_CACHE_KEY_PREFIX = "php-library-check-tools"
# Files at the library root whose content is part of the tool cache key:
# the Composer files and the configuration of PHPStan (*.neon), Rector and
# PHP-CS-Fixer, under their upstream names and the usual variants.
TOOL_CONFIG_GLOBS = (
    "composer.json", "composer.lock", "*.neon", "*.neon.dist", "*rector*.php", "*php-cs-fixer*.php", ".php_cs",
    ".php_cs.dist",
)


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
    expect_majors: tuple[tuple[str, int], ...] = ()
    dependency_versions: str = "locked"
    tool_cache_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolCache:
    """What action.yml passes to actions/cache; paths are relative to the checkout."""

    paths: tuple[str, ...]
    key: str
    restore_keys: tuple[str, ...]


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


def parse_expect_majors(value: str) -> tuple[tuple[str, int], ...]:
    """Parses expect-majors: <vendor>/<package>:<major> items, one per package."""
    items: dict[str, int] = {}
    for item in split_list(value):
        match = EXPECT_MAJOR_RE.fullmatch(item)
        if not match:
            raise CheckError(f"input expect-majors: {item!r} is not <vendor>/<package>:<major>")
        if match["name"] in items:
            raise CheckError(f"input expect-majors names {match['name']} twice")
        items[match["name"]] = int(match["major"])
    return tuple(items.items())


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
        expect_majors=parse_expect_majors(args.expect_majors),
        dependency_versions=check_dependency_versions(args.dependency_versions),
        tool_cache_paths=split_list(args.tool_cache_paths),
    )


def check_dependency_versions(value: str) -> str:
    if value not in DEPENDENCY_VERSIONS:
        raise CheckError(f"input dependency-versions must be locked or highest, got {value!r}")
    return value


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


def resolve_tool_cache_paths(root: Path, library_dir: Path, items: Sequence[str]) -> tuple[str, ...]:
    """Resolves each tool-cache-paths item inside path; returns them relative to the checkout.

    Refuses an absolute path, a glob, a path that leaves path, path itself
    and vendor: a restore there would replace the code or the dependencies
    under check.
    """
    resolved: list[str] = []
    for item in items:
        if Path(item).is_absolute() or UNSAFE_CACHE_PATH_RE.search(item) or ".." in PurePosixPath(item).parts:
            raise CheckError(f"input tool-cache-paths: {item!r} is not a plain path relative to path")
        target = (library_dir / item).resolve()
        if library_dir not in target.parents:
            raise CheckError(f"input tool-cache-paths: {item!r} is not inside path")
        if target.relative_to(library_dir).parts[0] == "vendor":
            raise CheckError(f"input tool-cache-paths: {item!r} is inside vendor")
        relative = target.relative_to(root).as_posix()
        if relative in resolved:
            raise CheckError(f"input tool-cache-paths names {item!r} twice")
        resolved.append(relative)
    return tuple(resolved)


def tool_config_hash(library_dir: Path) -> str:
    """SHA-256 of the name and content of each file of TOOL_CONFIG_GLOBS at the library root."""
    names = sorted({match.name for pattern in TOOL_CONFIG_GLOBS for match in library_dir.glob(pattern) if match.is_file()})
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256((library_dir / name).read_bytes()).digest())
    return digest.hexdigest()


def tool_cache(options: Options, root: Path, library_dir: Path, runner_os: str, commit: str) -> ToolCache | None:
    """Plans the tool cache, or None when tool-cache-paths is empty or the static checks are off.

    The key ends with the commit, so every successful job saves the caches
    of its commit; the one restore key falls back to the latest entry with
    the same runner OS, PHP version, dependency versions, path and Composer
    and tool files. A change to composer.json, composer.lock or a tool
    configuration starts from an empty cache, since not every tool checks
    its cache against the installed dependencies. PHPStan, Rector and
    PHP-CS-Fixer check their own cache against the code, so an older entry
    only costs the files that changed.
    """
    paths = resolve_tool_cache_paths(root, library_dir, options.tool_cache_paths)
    if not paths or not options.static_checks:
        return None
    if not RUNNER_OS_RE.fullmatch(runner_os):
        raise CheckError(f"runner OS {runner_os!r} is not a plain name")
    if not COMMIT_RE.fullmatch(commit):
        raise CheckError(f"commit {commit!r} is not a full commit SHA")
    library = library_dir.relative_to(root).as_posix() if library_dir != root else "."
    path_id = hashlib.sha256(library.encode("utf-8")).hexdigest()[:12]
    prefix = f"{TOOL_CACHE_KEY_PREFIX}-{runner_os}-php{options.php_version}-{options.dependency_versions}-{path_id}-"
    files = f"{prefix}{tool_config_hash(library_dir)}-"
    return ToolCache(paths=paths, key=f"{files}{commit}", restore_keys=(files,))


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
    """Appends each value to GITHUB_OUTPUT; a multi-line value in the delimiter form."""
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            if "\n" not in value:
                handle.write(f"{key}={value}\n")
                continue
            delimiter = f"EOF_{hashlib.sha256(value.encode('utf-8')).hexdigest()}"
            handle.write(f"{key}<<{delimiter}\n{value}\n{delimiter}\n")


def command_plan(args: argparse.Namespace) -> int:
    options = parse_options(args)
    dry_run = parse_bool("dry-run", args.dry_run)
    root = Path(args.workspace).resolve()
    library_dir = resolve_library_dir(root, options.path)
    cache = tool_cache(options, root, library_dir, args.runner_os, args.commit)
    plan = build_plan(options)
    plan_file = Path(args.plan_file)
    expect_majors = dict(options.expect_majors)
    plan_file.write_text(
        json.dumps({"directory": str(library_dir), "commands": plan, "expect-majors": expect_majors}),
        encoding="utf-8",
    )
    kind = "static checks and tests" if options.static_checks else "tests"
    print(f"PHP {options.php_version}: {kind}.")
    for name, major in options.expect_majors:
        print(("Would check: " if dry_run else "Will check: ") + f"{name} installed at major version {major}")
    if cache:
        print(("Would" if dry_run else "Will") + f" restore and save the tool caches {', '.join(cache.paths)}, key {cache.key}")
    for command in plan:
        print(("Would run: " if dry_run else "Will run: ") + " ".join(command))
    write_outputs(args.github_output, {
        "commands": json.dumps([" ".join(command) for command in plan]),
        "expect-majors": json.dumps(expect_majors),
        "plan-file": str(plan_file),
        "tool-cache-paths": json.dumps(list(cache.paths) if cache else []),
        "tool-cache-path-lines": "\n".join(cache.paths) if cache else "",
        "tool-cache-key": cache.key if cache else "",
        "tool-cache-restore-keys": "\n".join(cache.restore_keys) if cache else "",
    })
    return 0


def installed_versions(directory: str) -> dict[str, str]:
    """Returns {package name: version} of the installed packages."""
    result = subprocess.run(list(SHOW_COMMAND), cwd=directory, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise CheckError(f"{' '.join(SHOW_COMMAND)} failed with exit code {result.returncode}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise CheckError(f"{' '.join(SHOW_COMMAND)} did not print JSON") from None
    # Without any installed package, Composer prints an empty JSON array.
    installed = data.get("installed", []) if isinstance(data, dict) else []
    return {package.get("name", ""): package.get("version", "") for package in installed if isinstance(package, dict)}


def major_mismatches(expected: Mapping[str, int], installed: Mapping[str, str]) -> list[str]:
    """Returns one message per package of expected that is missing or at another major."""
    mismatches = []
    for name, major in expected.items():
        version = installed.get(name)
        if version is None:
            mismatches.append(f"{name} is not installed (expected major version {major})")
            continue
        match = INSTALLED_MAJOR_RE.match(version)
        if not match or int(match.group(1)) != major:
            mismatches.append(f"{name} {version} is installed, expected major version {major}")
    return mismatches


def check_majors(directory: str, expected: Mapping[str, int]) -> None:
    if not expected:
        return
    installed = installed_versions(directory)
    mismatches = major_mismatches(expected, installed)
    if mismatches:
        raise CheckError("expect-majors: " + "; ".join(mismatches))
    for name in expected:
        print(f"{name} {installed[name]}: major version {expected[name]} as expected.")


def command_run(args: argparse.Namespace) -> int:
    plan = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
    check_majors(plan["directory"], plan.get("expect-majors", {}))
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
    plan.add_argument("--expect-majors", default="")
    plan.add_argument("--dependency-versions", default="locked")
    plan.add_argument("--tool-cache-paths", default="")
    plan.add_argument("--runner-os", default="")
    plan.add_argument("--commit", default="")
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
