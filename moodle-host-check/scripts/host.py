"""Installs a throwaway Moodle host and runs a probe script against it.

Two subcommands, both called by action.yml:

  plan  validates the inputs and writes the plan: the commands that build the
        host and run the probe, as argument lists with placeholders for the
        directories of the runner. Writes nothing else; a dry run stops here.
  run   runs the plan: clones Moodle, installs its Composer dependencies when
        it has any, writes config.php, installs the database and runs the
        probe. Stops at the first failure.

Standard library only. Run as ``python3 -I``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from secrets import token_urlsafe
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

BRANCH_RE = re.compile(r"^(MOODLE_[0-9]{2,3}_STABLE|main)$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PHP_VERSION_RE = re.compile(r"^\d+\.\d+$")
PHP_EXTENSIONS_RE = re.compile(r"^[A-Za-z0-9_:, -]*$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_PORT = 65535
# Moodle requires max_input_vars >= 5000; the PHP default is 1000.
PHP_INI = ("-d", "max_input_vars=5000")
# Placeholders of the plan, replaced by real directories in "run".
MOODLE = "<moodle>"
PROBE = "<probe>"
SITE_EMAIL = "admin@example.com"
BUILTIN_PROBE = Path(__file__).resolve().parent / "probe.php"


class HostError(Exception):
    """A finding that stops the check; the message names the input or file."""


@dataclass(frozen=True)
class Options:
    moodle_branch: str
    moodle_repository: str
    php_version: str
    php_extensions: str
    path: str
    probe_script: str
    db_host: str
    db_port: int
    db_name: str
    db_user: str


def parse_bool(name: str, value: str) -> bool:
    if value in ("true", "false"):
        return value == "true"
    raise HostError(f"input {name} must be true or false, got {value!r}")


def check_pattern(name: str, value: str, pattern: re.Pattern[str], example: str) -> str:
    if not pattern.fullmatch(value):
        raise HostError(f"input {name} must look like {example}, got {value!r}")
    return value


def parse_port(value: str) -> int:
    if not value.isdigit() or not 0 < int(value) <= MAX_PORT:
        raise HostError(f"input db-port must be a TCP port, got {value!r}")
    return int(value)


def parse_options(args: argparse.Namespace) -> Options:
    if not PHP_EXTENSIONS_RE.fullmatch(args.php_extensions):
        raise HostError("input php-extensions holds characters other than letters, digits, _ : , - and spaces")
    return Options(
        moodle_branch=check_pattern("moodle-branch", args.moodle_branch, BRANCH_RE, "MOODLE_405_STABLE or main"),
        moodle_repository=check_pattern("moodle-repository", args.moodle_repository, REPOSITORY_RE, "moodle/moodle"),
        php_version=check_pattern("php-version", args.php_version, PHP_VERSION_RE, "8.3"),
        php_extensions=args.php_extensions,
        path=args.path,
        probe_script=args.probe_script,
        db_host=check_pattern("db-host", args.db_host, HOST_RE, "127.0.0.1"),
        db_port=parse_port(args.db_port),
        db_name=check_pattern("db-name", args.db_name, IDENTIFIER_RE, "moodle"),
        db_user=check_pattern("db-user", args.db_user, IDENTIFIER_RE, "postgres"),
    )


def inside(workspace: Path, name: str, value: str) -> Path:
    """Resolves value inside the workspace; refuses a path that leaves it."""
    if not value or Path(value).is_absolute():
        raise HostError(f"input {name} must be a relative path, got {value!r}")
    root = workspace.resolve()
    resolved = (root / value).resolve()
    if resolved != root and root not in resolved.parents:
        raise HostError(f"input {name} {value!r} leaves the checkout")
    return resolved


def resolve_code_dir(workspace: Path, path: str) -> Path:
    code_dir = inside(workspace, "path", path)
    if not (code_dir / "composer.json").is_file():
        raise HostError(f"input path {path!r} has no composer.json")
    return code_dir


def resolve_probe(workspace: Path, probe_script: str) -> Path:
    if not probe_script:
        return BUILTIN_PROBE
    probe = inside(workspace, "probe-script", probe_script)
    if probe.suffix != ".php" or not probe.is_file():
        raise HostError(f"input probe-script {probe_script!r} is not a .php file of the checkout")
    return probe


def clone_command(options: Options) -> list[str]:
    url = f"https://github.com/{options.moodle_repository}.git"
    return ["git", "clone", "--quiet", "--depth", "1", "--branch", options.moodle_branch, url, MOODLE]


def install_command() -> list[str]:
    return [
        "php", *PHP_INI, f"{MOODLE}/admin/cli/install_database.php",
        "--agree-license", "--lang=en", "--adminuser=admin", "--adminpass=<generated>",
        f"--adminemail={SITE_EMAIL}", f"--supportemail={SITE_EMAIL}",
        "--fullname=Moodle host check", "--shortname=host-check",
    ]


def build_plan(options: Options, probe_label: str) -> list[list[str]]:
    return [
        clone_command(options),
        ["composer", "install", "--no-dev", "--no-interaction", "--prefer-dist", f"--working-dir={MOODLE}"],
        install_command(),
        ["php", *PHP_INI, probe_label],
    ]


def probe_label(workspace: Path, probe: Path) -> str:
    if probe == BUILTIN_PROBE:
        return "<builtin-probe>"
    return probe.relative_to(workspace.resolve()).as_posix()


def write_outputs(github_output: str, outputs: Mapping[str, str]) -> None:
    if not github_output:
        return
    with open(github_output, "a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


def run_plan(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace)
    options = parse_options(args)
    dry_run = parse_bool("dry-run", args.dry_run)
    code_dir = resolve_code_dir(workspace, options.path)
    probe = resolve_probe(workspace, options.probe_script)
    plan = build_plan(options, probe_label(workspace, probe))
    commands = [" ".join(command) for command in plan]
    record = {**options.__dict__, "code_dir": str(code_dir), "probe": str(probe)}
    Path(args.plan_file).write_text(json.dumps(record), encoding="utf-8")
    print(f"Moodle host: {options.moodle_repository} {options.moodle_branch} on PHP {options.php_version}")
    for command in commands:
        print(("would run: " if dry_run else "plan: ") + command)
    write_outputs(args.github_output, {
        "plan-file": args.plan_file,
        "commands": json.dumps(commands),
    })
    return 0


def php_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def config_php(options: Options, dataroot: Path) -> str:
    """Moodle config.php for a PostgreSQL host; dbpass is empty (trust auth)."""
    values = {
        "dbtype": "pgsql",
        "dblibrary": "native",
        "dbhost": options.db_host,
        "dbname": options.db_name,
        "dbuser": options.db_user,
        "dbpass": "",
        "prefix": "m_",
        "wwwroot": "http://localhost",
        "dataroot": str(dataroot),
        "admin": "admin",
    }
    lines = ["<?php", "unset($CFG);", "global $CFG;", "$CFG = new stdClass();"]
    lines += [f"$CFG->{key} = {php_string(value)};" for key, value in values.items()]
    lines.append(f"$CFG->dboptions = ['dbpersist' => 0, 'dbport' => {options.db_port}];")
    lines.append("$CFG->directorypermissions = 02777;")
    lines.append("require_once(__DIR__ . '/lib/setup.php');")
    return "\n".join(lines) + "\n"


def needs_composer_install(moodle_dir: Path) -> bool:
    """True when Moodle requires Composer packages (Moodle 5.2 and later)."""
    manifest = moodle_dir / "composer.json"
    if not manifest.is_file():
        return False
    try:
        require = json.loads(manifest.read_text(encoding="utf-8")).get("require", {})
    except (json.JSONDecodeError, AttributeError) as error:
        raise HostError(f"Moodle composer.json is not a JSON object: {error}") from error
    return any("/" in name for name in require)


def substitute(command: Sequence[str], values: Mapping[str, str]) -> list[str]:
    result = []
    for part in command:
        for placeholder, value in values.items():
            part = part.replace(placeholder, value)
        result.append(part)
    return result


def execute(command: Sequence[str], shown: Sequence[str], cwd: Path, env: Mapping[str, str]) -> None:
    print("+ " + " ".join(shown), flush=True)
    result = subprocess.run(list(command), cwd=cwd, env=dict(env), check=False)
    if result.returncode != 0:
        raise HostError(f"command failed with exit status {result.returncode}: {' '.join(shown)}")


def load_options(plan_file: str) -> tuple[Options, Path, Path]:
    record = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    code_dir = Path(record.pop("code_dir"))
    probe = Path(record.pop("probe"))
    return Options(**record), code_dir, probe


def run_host(args: argparse.Namespace) -> int:
    options, code_dir, probe = load_options(args.plan_file)
    work = Path(args.work_dir)
    moodle_dir, dataroot = work / "moodle", work / "moodledata"
    dataroot.mkdir(parents=True, exist_ok=True)
    # Throwaway host: the password is never printed; the log shows the
    # <generated> placeholder of the plan in its place.
    password = "Aa1!" + token_urlsafe(18)
    clone, composer, install, run_probe = build_plan(options, str(probe))
    values = {MOODLE: str(moodle_dir)}
    env = {**os.environ, "MOODLE_HOST_CHECK_CONFIG": str(moodle_dir / "config.php"),
           "MOODLE_HOST_CHECK_CODE_DIR": str(code_dir)}
    execute(substitute(clone, values), substitute(clone, values), work, env)
    if needs_composer_install(moodle_dir):
        execute(substitute(composer, values), substitute(composer, values), work, env)
    (moodle_dir / "config.php").write_text(config_php(options, dataroot), encoding="utf-8")
    shown = substitute(install, values)
    secret = [part.replace("<generated>", password) for part in shown]
    execute(secret, shown, moodle_dir, env)
    execute(run_probe, run_probe, code_dir, env)
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    for name, default in (
        ("--workspace", "."), ("--moodle-branch", ""), ("--moodle-repository", "moodle/moodle"),
        ("--php-version", ""), ("--php-extensions", ""), ("--path", "."), ("--probe-script", ""),
        ("--db-host", "127.0.0.1"), ("--db-port", "5432"), ("--db-name", "moodle"),
        ("--db-user", "postgres"), ("--dry-run", "false"), ("--plan-file", ""), ("--github-output", ""),
    ):
        plan.add_argument(name, default=default)
    run = commands.add_parser("run")
    run.add_argument("--plan-file", required=True)
    run.add_argument("--work-dir", required=True)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "plan":
            return run_plan(args)
        return run_host(args)
    except HostError as error:
        print(f"::error::{error}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
