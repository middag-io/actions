"""Checks that a pull request asks for a MAJOR version only explicitly.

release-please cuts a MAJOR version from a "!" in a Conventional Commits
header or from a "BREAKING CHANGE:" footer. This check makes a MAJOR an
explicit decision instead: a pull request that carries a breaking change
marker must also carry a "Release-As: X.0.0" footer, with X above the MAJOR
of the package version in the release-please manifest of the base commit.

The check reads the Conventional Commits header of the pull request title
and the header and footers of every commit of the pull request that is not
a merge commit (commits base..head), and fails when:

- a header has "!" before ":" or a message has a "BREAKING CHANGE:" or
  "BREAKING-CHANGE:" footer, and no commit of the pull request has a
  "Release-As: X.0.0" footer with X above the base MAJOR;
- a "Release-As:" footer names a version that is not SemVer, that raises
  the MAJOR to anything but X.0.0, or that is not above the base version.

A "Release-As:" footer of a MINOR or a PATCH above the base version passes.
The title carries no footer: release-please reads footers from commits.

The header match is lenient (any case, white space around "(scope)", "!"
and ":"), so that it recognises at least what the release tooling reads as
a breaking change. Every other message passes, a message that is not a
Conventional Commit included: this check gates the MAJOR, not the format.

Exit status: 0 when the pull request passes (and always in a dry run, which
reports instead), 1 on a finding or an invalid input. Every problem is
printed as a GitHub annotation.

Standard library only. Run: python3 -I check.py --help
"""

import argparse
import json
import os
import re
import subprocess
import sys

SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
# A repository-relative path: no absolute path, no "..", no empty segment.
PATH_RE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/-]+$")
# A manifest key: "." or a repository-relative path.
PACKAGE_RE = re.compile(r"^(\.|(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/-]+)$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# Conventional Commits header: type, optional scope, optional "!", ":". Lenient
# on purpose (white space, no space after ":"): a near miss is still gated.
HEADER_RE = re.compile(r"^\s*(?P<type>[A-Za-z]+)\s*(?:\([^()\r\n]*\)\s*)?(?P<bang>!)?\s*:")
# Breaking change footer, at the start of a line after the header.
BREAKING_FOOTER_RE = re.compile(r"^[ \t]*BREAKING[ -]CHANGE[ \t]*:", re.MULTILINE)
# Release-As footer, at the start of a line after the header, any case.
RELEASE_AS_RE = re.compile(r"^[ \t]*release[ -]as[ \t]*:[ \t]*(?P<version>\S*)", re.MULTILINE | re.IGNORECASE)

# Separators of the git log records, and the git format escapes that print them
# (an argument of a process cannot hold a NUL byte).
COMMIT_SEPARATOR, COMMIT_SEPARATOR_FORMAT = "\x1e", "%x1e"
FIELD_SEPARATOR, FIELD_SEPARATOR_FORMAT = "\x00", "%x00"
SHORT_SHA = 7

GUIDANCE = (
    "How to proceed: a MAJOR version is an explicit decision. If this change is meant to start a new MAJOR, "
    "add a commit to this pull request whose message ends with the footer \"Release-As: X.0.0\", where X is the "
    "next MAJOR (an empty commit is enough), then push. If it is not, the change must stay compatible: a "
    "removal is first a deprecation in a MINOR. Do not rewrite this branch (never rebase or force-push): open "
    "a new pull request whose commits carry the right type instead."
)


class CheckError(Exception):
    """A problem in the inputs or in the files they point to."""


def parse_bool(name, value):
    """Returns the boolean of a "true"/"false" input."""
    if value in ("true", "false"):
        return value == "true"
    raise CheckError(f"input {name} must be true or false, got {value!r}")


def parse_version(name, value):
    """Returns (major, minor, patch, prerelease identifiers) of a SemVer version."""
    match = SEMVER_RE.match(value)
    if not match:
        raise CheckError(f"{name} must be a SemVer version, got {value!r}")
    prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease


def format_version(version):
    core = f"{version[0]}.{version[1]}.{version[2]}"
    return f"{core}-{'.'.join(version[3])}" if version[3] else core


def _identifier_key(identifier):
    # SemVer: numeric identifiers sort before alphanumeric ones.
    return (0, int(identifier), "") if identifier.isdigit() else (1, 0, identifier)


def version_above(version, base):
    """True when version > base under SemVer precedence."""
    if version[:3] != base[:3]:
        return version[:3] > base[:3]
    if not version[3] or not base[3]:
        return not version[3] and bool(base[3])
    return [_identifier_key(i) for i in version[3]] > [_identifier_key(i) for i in base[3]]


def is_breaking(message):
    """True when a commit message or a title carries "!" in its header or a breaking change footer."""
    lines = message.splitlines()
    header = lines[0] if lines else ""
    match = HEADER_RE.match(header)
    if match and match.group("bang"):
        return True
    return bool(BREAKING_FOOTER_RE.search("\n".join(lines[1:])))


def release_as_versions(message):
    """Returns the versions of the Release-As footers of a message (after its header)."""
    body = "\n".join(message.splitlines()[1:])
    return [match.group("version") for match in RELEASE_AS_RE.finditer(body)]


def classify_release_as(text, base):
    """Returns (kind, problem) of one Release-As value: kind is major, minor-patch or None."""
    match = SEMVER_RE.match(text.lstrip("vV"))
    if not text or not match:
        return None, f"is not a SemVer version ({text or 'empty'})"
    version = parse_version("Release-As", text.lstrip("vV"))
    shown = format_version(version)
    if version[0] > base[0]:
        if version[1:] != (0, 0, ()):
            return None, f"raises the MAJOR to {shown}; a new MAJOR is released as exactly {version[0]}.0.0"
        return "major", None
    if not version_above(version, base):
        return None, f"names {shown}, which is not above the base version {format_version(base)}"
    return "minor-patch", None


def evaluate(base, messages):
    """Returns (findings, major) of (label, message) pairs; major is the explicit MAJOR or None."""
    findings, majors = [], []
    for label, message in messages:
        for text in release_as_versions(message):
            kind, problem = classify_release_as(text, base)
            if problem:
                findings.append(f"{label}: the Release-As footer {problem}")
            elif kind == "major":
                majors.append(text.lstrip("vV"))
    major = majors[0] if majors else None
    if len(set(majors)) > 1:
        findings.append(f"the pull request names more than one MAJOR in Release-As footers ({', '.join(sorted(set(majors)))})")
    if major is None:
        expected = f"{base[0] + 1}.0.0"
        for label, message in messages:
            if is_breaking(message):
                findings.append(
                    f"{label}: a breaking change (\"!\" or a BREAKING CHANGE footer) releases a new MAJOR only "
                    f"with an explicit \"Release-As: {expected}\" footer in a commit of this pull request; the "
                    f"base version is {format_version(base)}"
                )
    return findings, major


def manifest_version(text, manifest_file, package):
    """Returns the version of package in the manifest text."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise CheckError(f"input manifest-file: {manifest_file} is not valid JSON ({error})") from error
    if not isinstance(data, dict):
        raise CheckError(f"input manifest-file: {manifest_file} must hold a JSON object")
    if package not in data:
        raise CheckError(f"input package: {package!r} is not a package of {manifest_file}")
    value = data[package]
    if not isinstance(value, str):
        raise CheckError(f"{manifest_file}: the version of {package!r} must be a string")
    return value


def run_git(workspace, *args):
    """Runs git in workspace and returns its standard output."""
    result = subprocess.run(
        ["git", "-C", workspace, *args], capture_output=True, text=True, encoding="utf-8", check=False
    )
    if result.returncode != 0:
        raise CheckError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def read_commits(git, base_sha, head_sha):
    """Returns [(label, message)] of the commits base..head that are not merge commits."""
    output = git("log", f"--format=%H{FIELD_SEPARATOR_FORMAT}%P{FIELD_SEPARATOR_FORMAT}%B{COMMIT_SEPARATOR_FORMAT}", f"{base_sha}..{head_sha}")
    commits = []
    for record in output.split(COMMIT_SEPARATOR):
        record = record.strip("\n")
        if not record:
            continue
        sha, parents, message = record.split(FIELD_SEPARATOR, 2)
        if len(parents.split()) > 1:
            continue
        header = message.splitlines()[0] if message else ""
        commits.append((f"commit {sha[:SHORT_SHA]} ({header})", message))
    return commits


def annotate(level, message):
    """Prints a GitHub annotation; newlines are encoded as the format requires."""
    text = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level}::{text}")


def write_outputs(path, outputs):
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


def load_inputs(args):
    """Validates every input before any read; returns dry_run."""
    dry_run = parse_bool("dry-run", args.dry_run)
    if not PATH_RE.match(args.manifest_file):
        raise CheckError(f"input manifest-file must be a path relative to the repository root, got {args.manifest_file!r}")
    if not PACKAGE_RE.match(args.package):
        raise CheckError(f"input package must be \".\" or a path relative to the repository root, got {args.package!r}")
    if not args.title.strip():
        raise CheckError("input title is empty: run this check on a pull_request event or pass the title")
    if not dry_run:
        for name, value in (("base-sha", args.base_sha), ("head-sha", args.head_sha)):
            if not SHA_RE.match(value):
                raise CheckError(f"input {name} must be a full commit SHA; run this check on a pull_request event")
    return dry_run


def read_manifest(args, git, dry_run):
    """Returns the manifest text: from the workspace in a dry run, else at the base commit."""
    if not dry_run:
        return git("show", f"{args.base_sha}:{args.manifest_file}")
    path = os.path.join(args.workspace, args.manifest_file)
    if not os.path.isfile(path):
        raise CheckError(f"input manifest-file: file {args.manifest_file} not found in the workspace")
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def check(args, git=None):
    """Runs the check; returns the exit status."""
    git = git or (lambda *a: run_git(args.workspace, *a))
    dry_run = load_inputs(args)
    version_text = manifest_version(read_manifest(args, git, dry_run), args.manifest_file, args.package)
    base = parse_version(f"version of {args.package!r} in {args.manifest_file}", version_text)
    print(f"version {version_text} of {args.package!r}: a new MAJOR needs \"Release-As: {base[0] + 1}.0.0\" or above")

    messages = [("pull request title", args.title)]
    if dry_run:
        print("dry run: the commits base..head are not read; only the title is checked")
    else:
        messages += read_commits(git, args.base_sha, args.head_sha)
    findings, major = evaluate(base, messages)
    print(f"{len(messages)} message(s) checked, {len(findings)} finding(s)")

    write_outputs(args.github_output, {
        "version": version_text,
        "release-as-major": major or "",
        "result": "fail" if findings else "pass",
        "findings": len(findings),
    })
    level = "warning" if dry_run else "error"
    for finding in findings:
        annotate(level, finding)
    if findings:
        annotate(level, GUIDANCE)
    return 0 if dry_run or not findings else 1


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest-file", default=".release-please-manifest.json")
    parser.add_argument("--package", default=".")
    parser.add_argument("--title", default="")
    parser.add_argument("--base-sha", default="")
    parser.add_argument("--head-sha", default="")
    parser.add_argument("--dry-run", default="false")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--github-output", default="")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return check(args)
    except CheckError as error:
        annotate("error", str(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())
