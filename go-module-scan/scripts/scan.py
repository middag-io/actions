"""Scans the packages of a Go module for known vulnerabilities with govulncheck.

Two subcommands, both called by action.yml:

  plan  validates the inputs and prints the scan command. Writes nothing
        else; a dry run stops here.
  run   installs govulncheck at a fixed version into a temporary directory,
        runs ``govulncheck -format json ./...``, prints every vulnerability
        once, and fails only when a vulnerability has a severity listed in
        blocking-severities.

The Go vulnerability database gives no severity. This script maps the
reachability that govulncheck reports to a severity: a vulnerable symbol that
the code calls is ``critical``; a vulnerable package that is only imported,
or a vulnerable module that is only required, is ``low``. With the default
(``critical``), the scan fails exactly when govulncheck itself would fail in
its text mode.

Standard library only. Run as ``python3 -I``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

SEVERITIES = ("critical", "high", "medium", "low")
TOOL = "golang.org/x/vuln/cmd/govulncheck@v1.9.0"
SCAN_ARGS = ("-format", "json", "./...")
SCAN_COMMAND = ("govulncheck", *SCAN_ARGS)
REACHABLE = "critical"
UNREACHABLE = "low"


class ScanError(Exception):
    """A finding that stops the scan; the message names the input or file."""


@dataclass(frozen=True)
class Vulnerability:
    osv: str
    aliases: tuple[str, ...]
    module: str
    version: str
    fixed_version: str
    called: str
    summary: str
    link: str

    @property
    def severity(self) -> str:
        return REACHABLE if self.called else UNREACHABLE


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
    if not (directory / "go.mod").is_file():
        raise ScanError(f"input working-directory {value!r} has no go.mod")
    return directory


def parse_stream(stdout: str) -> list[Mapping[str, object]]:
    """Reads the stream of JSON objects that govulncheck prints."""
    decoder, messages, index = json.JSONDecoder(), [], 0
    while True:
        while index < len(stdout) and stdout[index].isspace():
            index += 1
        if index >= len(stdout):
            break
        try:
            message, index = decoder.raw_decode(stdout, index)
        except json.JSONDecodeError as error:
            raise ScanError(f"govulncheck did not print a JSON report: {error}") from error
        if not isinstance(message, dict):
            raise ScanError("govulncheck report: a message is not an object")
        messages.append(message)
    if not any("config" in message for message in messages):
        raise ScanError("govulncheck did not print a JSON report; see its error output above")
    return messages


def called_symbol(frame: Mapping[str, object]) -> str:
    function = str(frame.get("function") or "")
    if not function:
        return ""
    receiver = str(frame.get("receiver") or "")
    name = f"{receiver}.{function}" if receiver else function
    return f"{frame.get('package') or frame.get('module')}.{name}"


def read_vulnerabilities(messages: Iterable[Mapping[str, object]]) -> list[Vulnerability]:
    """Reads each vulnerability once, at the deepest level govulncheck found.

    govulncheck prints one finding per level (module, package, symbol) and
    per call path; the first frame of a trace is the vulnerable one.
    """
    records, found = {}, {}
    for message in messages:
        if isinstance(message.get("osv"), dict):
            records[message["osv"].get("id")] = message["osv"]
        finding = message.get("finding")
        if not isinstance(finding, dict):
            continue
        trace = finding.get("trace") or []
        if not trace or not isinstance(trace[0], dict):
            raise ScanError(f"govulncheck report: finding {finding.get('osv')} has no trace")
        frame, osv = trace[0], str(finding.get("osv") or "")
        previous = found.get(osv)
        called = called_symbol(frame)
        if previous is None or (called and not previous[1]):
            found[osv] = (frame, called, str(finding.get("fixed_version") or ""))
    vulnerabilities = []
    for osv, (frame, called, fixed) in sorted(found.items()):
        record = records.get(osv) or {}
        specific = record.get("database_specific") or {}
        vulnerabilities.append(Vulnerability(
            osv=osv,
            aliases=tuple(str(alias) for alias in record.get("aliases") or ()),
            module=str(frame.get("module") or ""),
            version=str(frame.get("version") or ""),
            fixed_version=fixed,
            called=called,
            summary=str(record.get("summary") or ""),
            link=str(specific.get("url") or f"https://pkg.go.dev/vuln/{osv}"),
        ))
    return vulnerabilities


def describe(vulnerability: Vulnerability) -> str:
    name = vulnerability.osv
    if vulnerability.aliases:
        name += f" ({', '.join(vulnerability.aliases)})"
    module = vulnerability.module + (f"@{vulnerability.version}" if vulnerability.version else "")
    reach = f"called: {vulnerability.called}" if vulnerability.called else "not called"
    fixed = f"; fixed in {vulnerability.fixed_version}" if vulnerability.fixed_version else "; no fixed version"
    return f"{name}: {module}: {vulnerability.severity} ({reach}): {vulnerability.summary}{fixed} {vulnerability.link}"


def summarize(vulnerabilities: Iterable[Vulnerability], blocking: Sequence[str]) -> int:
    """Prints the findings; returns the number of blocking vulnerabilities."""
    count = 0
    for vulnerability in vulnerabilities:
        is_blocking = vulnerability.severity in blocking
        count += is_blocking
        print(f"::{'error' if is_blocking else 'warning'}::{describe(vulnerability)}".rstrip())
    return count


def write_outputs(github_output: str, outputs: Mapping[str, str]) -> None:
    if not github_output:
        return
    with open(github_output, "a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


def execute(command: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess:
    print("+ " + " ".join(command), flush=True)
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False, **kwargs)
    except FileNotFoundError as error:
        raise ScanError(f"{command[0]} not found; set up Go before this action") from error


def install_tool(bin_directory: str) -> Path:
    """Installs govulncheck into bin_directory, with the checksums of the Go sum database.

    GOTOOLCHAIN=auto lets an older Go download the toolchain that govulncheck
    needs; the scan itself runs with the Go of the job.
    """
    env = {**os.environ, "GOBIN": bin_directory, "GOTOOLCHAIN": "auto", "GOFLAGS": ""}
    result = execute(["go", "install", TOOL], env=env)
    if result.returncode != 0:
        raise ScanError(f"go install {TOOL} failed: {result.stderr.strip()}")
    return Path(bin_directory) / "govulncheck"


def run_plan(args: argparse.Namespace) -> int:
    blocking = parse_severities(args.blocking_severities)
    dry_run = parse_bool("dry-run", args.dry_run)
    resolve_directory(Path(args.workspace), args.working_directory)
    command = " ".join(SCAN_COMMAND)
    print(("would run: " if dry_run else "plan: ") + command + f" (govulncheck from {TOOL})")
    print("blocking severities: " + ", ".join(blocking))
    write_outputs(args.github_output, {"command": command, "tool": TOOL, "blocking-severities": ",".join(blocking)})
    return 0


def run_scan(args: argparse.Namespace) -> int:
    blocking = parse_severities(args.blocking_severities)
    directory = resolve_directory(Path(args.workspace), args.working_directory)
    with tempfile.TemporaryDirectory(prefix="go-module-scan-") as bin_directory:
        tool = install_tool(bin_directory)
        result = execute([str(tool), *SCAN_ARGS], cwd=directory)
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr, flush=True)
    if result.returncode != 0:
        raise ScanError(f"govulncheck failed with exit code {result.returncode}; see its error output above")
    vulnerabilities = read_vulnerabilities(parse_stream(result.stdout))
    count = summarize(vulnerabilities, blocking)
    write_outputs(args.github_output, {"advisories": str(len(vulnerabilities)), "blocking": str(count)})
    print(f"{len(vulnerabilities)} vulnerabilities, {count} with a blocking severity ({', '.join(blocking)})")
    if count:
        raise ScanError(f"{count} vulnerabilities with a blocking severity ({', '.join(blocking)})")
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
