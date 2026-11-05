"""Audits the resolved Composer dependencies for known vulnerabilities.

Two subcommands, both called by action.yml:

  plan  validates the inputs and prints the audit command. Writes nothing
        else; a dry run stops here.
  run   runs ``composer audit --format=json``, prints every advisory and every
        abandoned package, and fails only when an advisory has a severity
        listed in blocking-severities.

Composer applies its own ``audit.ignore`` configuration of composer.json
before this script reads the report. Standard library only. Run as
``python3 -I``.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

SEVERITIES = ("critical", "high", "medium", "low")
AUDIT_COMMAND = ("composer", "audit", "--format=json", "--abandoned=report", "--no-interaction")
INSTALLED = Path("vendor") / "composer" / "installed.json"


class ScanError(Exception):
    """A finding that stops the scan; the message names the input or file."""


@dataclass(frozen=True)
class Advisory:
    package: str
    severity: str
    title: str
    link: str


def parse_bool(name: str, value: str) -> bool:
    if value in ("true", "false"):
        return value == "true"
    raise ScanError(f"input {name} must be true or false, got {value!r}")


def parse_severities(value: str) -> tuple[str, ...]:
    """Reads a list separated by commas, spaces or new lines; at least one."""
    items = tuple(item for item in re.split(r"[,\s]+", value.lower()) if item)
    if not items:
        raise ScanError("input blocking-severities is empty; list at least one of " + ", ".join(SEVERITIES))
    unknown = [item for item in items if item not in SEVERITIES]
    if unknown:
        raise ScanError(f"input blocking-severities: {', '.join(unknown)} not in " + ", ".join(SEVERITIES))
    return tuple(severity for severity in SEVERITIES if severity in items)


def resolve_directory(workspace: Path, value: str) -> Path:
    """Resolves working-directory inside the workspace; refuses one that leaves it."""
    if not value or Path(value).is_absolute():
        raise ScanError(f"input working-directory must be a relative directory, got {value!r}")
    root = workspace.resolve()
    directory = (root / value).resolve()
    if directory != root and root not in directory.parents:
        raise ScanError(f"input working-directory {value!r} leaves the checkout")
    if not (directory / "composer.json").is_file():
        raise ScanError(f"input working-directory {value!r} has no composer.json")
    return directory


def audit_command(directory: Path) -> list[str]:
    """Audits the installed packages, or the lock file when nothing is installed."""
    if (directory / INSTALLED).is_file():
        return list(AUDIT_COMMAND)
    if (directory / "composer.lock").is_file():
        return [*AUDIT_COMMAND, "--locked"]
    raise ScanError("nothing to audit: no vendor/composer/installed.json and no composer.lock; run composer install first")


def read_advisories(report: Mapping[str, object]) -> list[Advisory]:
    """Reads the advisories of the report: a mapping of package to a list (or a mapping) of advisories."""
    found = report.get("advisories") or {}
    if not isinstance(found, dict):
        raise ScanError("composer audit report: advisories is not an object")
    advisories = []
    for package, entries in found.items():
        values = entries.values() if isinstance(entries, dict) else entries
        for entry in values:
            if not isinstance(entry, dict):
                raise ScanError(f"composer audit report: an advisory of {package} is not an object")
            advisories.append(Advisory(
                package=str(package),
                severity=str(entry.get("severity") or "unknown").lower(),
                title=str(entry.get("title") or ""),
                link=str(entry.get("link") or entry.get("advisoryId") or ""),
            ))
    return advisories


def read_abandoned(report: Mapping[str, object]) -> list[str]:
    abandoned = report.get("abandoned") or {}
    if not isinstance(abandoned, dict):
        return []
    lines = []
    for package, replacement in sorted(abandoned.items()):
        lines.append(f"{package} (use {replacement})" if replacement else f"{package} (no replacement)")
    return lines


def parse_report(stdout: str) -> Mapping[str, object]:
    start = stdout.find("{")
    try:
        report = json.loads(stdout[start:]) if start >= 0 else None
    except json.JSONDecodeError as error:
        raise ScanError(f"composer audit did not print a JSON report: {error}") from error
    if not isinstance(report, dict):
        raise ScanError("composer audit did not print a JSON report; see its error output above")
    return report


def summarize(advisories: Iterable[Advisory], abandoned: Sequence[str], blocking: Sequence[str]) -> int:
    """Prints the findings; returns the number of blocking advisories."""
    count = 0
    for advisory in advisories:
        is_blocking = advisory.severity in blocking
        count += is_blocking
        level = "error" if is_blocking else "warning"
        print(f"::{level}::{advisory.package}: {advisory.severity}: {advisory.title} {advisory.link}".rstrip())
    for line in abandoned:
        print(f"::warning::abandoned package: {line}")
    return count


def write_outputs(github_output: str, outputs: Mapping[str, str]) -> None:
    if not github_output:
        return
    with open(github_output, "a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


def run_plan(args: argparse.Namespace) -> int:
    blocking = parse_severities(args.blocking_severities)
    dry_run = parse_bool("dry-run", args.dry_run)
    resolve_directory(Path(args.workspace), args.working_directory)
    command = " ".join(AUDIT_COMMAND)
    print(("would run: " if dry_run else "plan: ") + command)
    print("blocking severities: " + ", ".join(blocking))
    write_outputs(args.github_output, {"command": command, "blocking-severities": ",".join(blocking)})
    return 0


def run_scan(args: argparse.Namespace) -> int:
    blocking = parse_severities(args.blocking_severities)
    directory = resolve_directory(Path(args.workspace), args.working_directory)
    command = audit_command(directory)
    print("+ " + " ".join(command), flush=True)
    result = subprocess.run(command, cwd=directory, capture_output=True, text=True, check=False)
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr, flush=True)
    report = parse_report(result.stdout)
    advisories = read_advisories(report)
    count = summarize(advisories, read_abandoned(report), blocking)
    write_outputs(args.github_output, {"advisories": str(len(advisories)), "blocking": str(count)})
    print(f"{len(advisories)} advisories, {count} with a blocking severity ({', '.join(blocking)})")
    if count:
        raise ScanError(f"{count} advisories with a blocking severity ({', '.join(blocking)})")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        sub = commands.add_parser(name)
        sub.add_argument("--workspace", default=".")
        sub.add_argument("--working-directory", default=".")
        sub.add_argument("--blocking-severities", default="critical")
        sub.add_argument("--github-output", default="")
        if name == "plan":
            sub.add_argument("--dry-run", default="false")
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return run_plan(args) if args.command == "plan" else run_scan(args)
    except ScanError as error:
        print(f"::error::{error}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
