"""Checks a pull request against the window of a version family.

A version family is a set of packages that share MAJOR.MINOR. A feature
waits for a family MINOR window, and a breaking change for a family MAJOR
window. The window comes from one input (usually an organization variable):

- empty: closed;
- "X.Y" with Y > 0: a family MINOR window;
- "X.0": a family MAJOR window.

A window counts as open only for a pull request into the window branch
(window-branch, given with base-ref), and only when it is ahead of the
version of the package in the release-please manifest of the base commit:
a MINOR window X.Y when X is the MAJOR of that version and Y is above its
MINOR, a MAJOR window X.0 when X.0.0 is above that version. Otherwise (a
pull request into a maintenance branch such as release/X.Y, or a window
left set after its release) it counts as closed.

The check reads the Conventional Commits header of the pull request title
and the header and footers of every commit of the pull request that is not
a merge commit (commits base..head), and fails when:

- a header has "!" before ":" or a message has a "BREAKING CHANGE:" or
  "BREAKING-CHANGE:" footer, unless a MAJOR window is open;
- a header has the type feat while no window is open, but only when the
  version of the package in the release-please manifest of the base commit
  is at least family-from (empty: always);
- a message has a "Release-As:" footer, unless a window is open and the
  footer names its first version X.Y.0: a family MINOR or MAJOR is one
  coordinated version, never one package's own.

The header match is lenient (any case, white space around "(scope)", "!"
and ":"), so that it recognises at least what the release tooling reads as
a feature or a breaking change.

Every other header passes, a header that is not a Conventional Commit
included: this check enforces the family window, not the commit format.

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

WINDOW_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)$")
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
# A branch name: no leading "/" or "-", no "..", no white space.
BRANCH_RE = re.compile(r"^(?![/-])(?!.*\.\.)[A-Za-z0-9._/-]+$")
# Conventional Commits header: type, optional scope, optional "!", ":". Lenient
# on purpose (white space, no space after ":"): a near miss is still gated.
HEADER_RE = re.compile(r"^\s*(?P<type>[A-Za-z]+)\s*(?:\([^()\r\n]*\)\s*)?(?P<bang>!)?\s*:")
# Breaking change footer, at the start of a line after the header.
BREAKING_FOOTER_RE = re.compile(r"^[ \t]*BREAKING[ -]CHANGE[ \t]*:", re.MULTILINE)
# Release-As footer, at the start of a line after the header, any case.
RELEASE_AS_RE = re.compile(r"^[ \t]*release[ -]as[ \t]*:[ \t]*(?P<version>\S*)", re.MULTILINE | re.IGNORECASE)

CLOSED, MINOR, MAJOR = "closed", "minor", "major"
# Separators of the git log records, and the git format escapes that print them
# (an argument of a process cannot hold a NUL byte).
COMMIT_SEPARATOR, COMMIT_SEPARATOR_FORMAT = "\x1e", "%x1e"
FIELD_SEPARATOR, FIELD_SEPARATOR_FORMAT = "\x00", "%x00"
SHORT_SHA = 7

GUIDANCE = (
    "How to proceed: a feature waits on its pull request for a family MINOR window, and a breaking "
    "change for a family MAJOR window. Keep this pull request open and keep it current by merging the "
    "base branch into it (never rebase or force-push). When the window opens, re-run this check, or "
    "push a merge of the base branch (a re-run reads the title and the commits of the original event). "
    "A family version (Release-As) is set by whoever coordinates the family release, not by one "
    "pull request. A removal is first a deprecation in a family MINOR and leaves in the next family MAJOR. If a commit "
    "carries the wrong type (for example a fix typed as feat), do not rewrite this branch: open a new "
    "pull request whose commits carry the right type."
)


class CheckError(Exception):
    """A problem in the inputs or in the files they point to."""


def parse_bool(name, value):
    """Returns the boolean of a "true"/"false" input."""
    if value in ("true", "false"):
        return value == "true"
    raise CheckError(f"input {name} must be true or false, got {value!r}")


def parse_window(value):
    """Returns (kind, label, target) of the window input: closed, minor or major.

    target is the first version of the window, (X, Y, 0, ()), or None when closed.
    """
    value = value.strip()
    if value == "":
        return CLOSED, "closed", None
    match = WINDOW_RE.match(value)
    if not match:
        raise CheckError(f"input window must be empty (closed) or MAJOR.MINOR such as 2.1 or 3.0, got {value!r}")
    kind = MAJOR if match.group(2) == "0" else MINOR
    return kind, f"{kind} window {value}", (int(match.group(1)), int(match.group(2)), 0, ())


def effective_window(kind, label, target, version, base_ref="", window_branch=""):
    """Returns (kind, label) of the window for this pull request: closed when it does not apply.

    The window applies to a pull request into window_branch (when both names
    are given), and when it is ahead of the base version: a MINOR window X.Y
    is ahead of a version of MAJOR X and MINOR below Y; a MAJOR window X.0 is
    ahead of a version below X.0.0.
    """
    if kind == CLOSED:
        return kind, label
    if base_ref and window_branch and base_ref != window_branch:
        return CLOSED, (
            f"closed ({label} applies to pull requests into {window_branch}; this one is into {base_ref})"
        )
    ahead = not version_at_least(version, target)
    if kind == MINOR:
        ahead = ahead and target[0] == version[0]
    if ahead:
        return kind, label
    base = format_version(version)
    return CLOSED, (
        f"closed ({label} is not ahead of version {base} of the base branch: the window was left set after "
        f"its release, or the base branch is on another line)"
    )


def format_version(version):
    core = f"{version[0]}.{version[1]}.{version[2]}"
    return f"{core}-{'.'.join(version[3])}" if version[3] else core


def parse_version(name, value):
    """Returns (major, minor, patch, prerelease identifiers) of a SemVer version."""
    match = SEMVER_RE.match(value)
    if not match:
        raise CheckError(f"{name} must be a SemVer version, got {value!r}")
    prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease


def _identifier_key(identifier):
    # SemVer: numeric identifiers sort before alphanumeric ones.
    return (0, int(identifier), "") if identifier.isdigit() else (1, 0, identifier)


def version_at_least(version, start):
    """True when version >= start under SemVer precedence."""
    if version[:3] != start[:3]:
        return version[:3] > start[:3]
    if not version[3]:
        return True
    if not start[3]:
        return False
    return [_identifier_key(i) for i in version[3]] >= [_identifier_key(i) for i in start[3]]


def parse_message(message):
    """Returns (type, breaking) of a commit message or a title; type is None when not conventional."""
    lines = message.splitlines()
    header = lines[0] if lines else ""
    match = HEADER_RE.match(header)
    body = "\n".join(lines[1:])
    breaking = bool(BREAKING_FOOTER_RE.search(body))
    if not match:
        return None, breaking
    return match.group("type").lower(), breaking or bool(match.group("bang"))


def release_as_versions(message):
    """Returns the versions of the Release-As footers of a message (after its header)."""
    body = "\n".join(message.splitlines()[1:])
    return [match.group("version") for match in RELEASE_AS_RE.finditer(body)]


def evaluate(window_kind, feat_gated, messages, window_label=None, window_target=None):
    """Returns the findings of (label, message) pairs, one string each.

    window_kind is the effective kind (closed when the window is not ahead of
    the base version); window_target is its first version, or None.
    """
    window_label = window_label or window_kind
    allowed_release = format_version(window_target) if window_kind != CLOSED and window_target else None
    findings = []
    for label, message in messages:
        commit_type, breaking = parse_message(message)
        if breaking and window_kind != MAJOR:
            findings.append(
                f"{label}: a breaking change (\"!\" or a BREAKING CHANGE footer) needs a family MAJOR window "
                f"(window X.0); the window is {window_label}"
            )
        elif commit_type == "feat" and feat_gated and window_kind == CLOSED:
            findings.append(f"{label}: type feat needs an open family window (MINOR X.Y or MAJOR X.0); the window is {window_label}")
        for version in release_as_versions(message):
            if allowed_release is None or version.lstrip("vV") != allowed_release:
                expected = f"only Release-As: {allowed_release}" if allowed_release else "no Release-As"
                findings.append(
                    f"{label}: a Release-As footer ({version or 'empty'}) cuts a version outside the family; "
                    f"the window is {window_label}, which allows {expected}"
                )
    return findings


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
    """Validates every input before any read; returns the parsed values."""
    dry_run = parse_bool("dry-run", args.dry_run)
    window = parse_window(args.window)
    if not PATH_RE.match(args.manifest_file):
        raise CheckError(f"input manifest-file must be a path relative to the repository root, got {args.manifest_file!r}")
    if not PACKAGE_RE.match(args.package):
        raise CheckError(f"input package must be \".\" or a path relative to the repository root, got {args.package!r}")
    family_from = parse_version("input family-from", args.family_from) if args.family_from else None
    for name, value in (("base-ref", args.base_ref), ("window-branch", args.window_branch)):
        if value and not BRANCH_RE.match(value):
            raise CheckError(f"input {name} must be a branch name, got {value!r}")
    if not args.title.strip():
        raise CheckError("input title is empty: run this check on a pull_request event or pass the title")
    if not dry_run:
        for name, value in (("base-sha", args.base_sha), ("head-sha", args.head_sha)):
            if not SHA_RE.match(value):
                raise CheckError(f"input {name} must be a full commit SHA; run this check on a pull_request event")
    return dry_run, window, family_from


def check(args, git=None):
    """Runs the check; returns the exit status."""
    git = git or (lambda *a: run_git(args.workspace, *a))
    dry_run, (window_kind, window_label, window_target), family_from = load_inputs(args)
    if dry_run:
        path = os.path.join(args.workspace, args.manifest_file)
        if not os.path.isfile(path):
            raise CheckError(f"input manifest-file: file {args.manifest_file} not found in the workspace")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    else:
        text = git("show", f"{args.base_sha}:{args.manifest_file}")
    version_text = manifest_version(text, args.manifest_file, args.package)
    version = parse_version(f"version of {args.package!r} in {args.manifest_file}", version_text)
    feat_gated = family_from is None or version_at_least(version, family_from)
    window_kind, window_label = effective_window(
        window_kind, window_label, window_target, version, args.base_ref, args.window_branch
    )
    print(f"version {version_text} of {args.package!r}; {window_label}; feat {'needs a window' if feat_gated else 'passes (below family-from)'}")

    messages = [("pull request title", args.title)]
    if dry_run:
        print("dry run: the commits base..head are not read; only the title is checked")
    else:
        messages += read_commits(git, args.base_sha, args.head_sha)
    findings = evaluate(window_kind, feat_gated, messages, window_label, window_target)
    print(f"{len(messages)} message(s) checked, {len(findings)} finding(s)")

    write_outputs(args.github_output, {
        "version": version_text,
        "window-kind": window_kind,
        "result": "fail" if findings else "pass",
        "findings": len(findings),
    })
    for finding in findings:
        annotate("warning" if dry_run else "error", finding)
    if findings:
        annotate("warning" if dry_run else "error", GUIDANCE)
    return 0 if dry_run or not findings else 1


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--window", default="")
    parser.add_argument("--manifest-file", default=".release-please-manifest.json")
    parser.add_argument("--package", default=".")
    parser.add_argument("--family-from", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--base-sha", default="")
    parser.add_argument("--head-sha", default="")
    parser.add_argument("--base-ref", default="")
    parser.add_argument("--window-branch", default="")
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
