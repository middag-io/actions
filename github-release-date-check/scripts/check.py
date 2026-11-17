"""Checks that no commit of a pull request is dated before the last release.

release-please walks the history of the base branch from its tip, newest
committer date first, and stops at the commit of the last release. A commit
whose committer date is earlier than that release commit comes after it in
that walk: release-please never reads it, so the change is missing from the
changelog and does not count for the next version (a "feat" lost this way
releases a PATCH instead of a MINOR). This happens to a branch whose commits
were made before the last release and merged after it.

The check reads the version of the package in the release-please manifest
of the base branch, finds the commit of its release tag, and fails when a
commit of the pull request that is not a merge commit (commits base..head)
has a committer date that is not later than the committer date of the
release commit. The base is the tip of the base branch when the check runs
(refs/remotes/origin/<base-ref>), not the base of the event, so that a
re-run sees a release that landed after the pull request was opened.

A missing release tag (no release yet, or the tag of a release that is
still being created) is a warning and the check passes: there is no
boundary to compare with.

Exit status: 0 when the pull request passes (and always in a dry run, which
only reports), 1 on a finding or an invalid input. Every problem is printed
as a GitHub annotation.

Standard library only. Run: python3 -I check.py --help
"""

import argparse
import json
import os
import re
import subprocess
import sys

# A repository-relative path: no absolute path, no "..", no empty segment.
PATH_RE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/-]+$")
# A manifest key: "." or a repository-relative path.
PACKAGE_RE = re.compile(r"^(\.|(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/-]+)$")
# A branch name: the characters git accepts in practice, no "..", no leading "-" or "/".
BRANCH_RE = re.compile(r"^(?![-/])(?!.*\.\.)(?!.*//)[A-Za-z0-9._/-]+(?<![./])$")
# A tag format: the characters of a tag name around exactly one "{version}".
TAG_FORMAT_RE = re.compile(r"^[A-Za-z0-9._/@-]*\{version\}[A-Za-z0-9._/@-]*$")
VERSION_RE = re.compile(r"^[0-9A-Za-z.+-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# Separators of the git log records, and the git format escapes that print them
# (an argument of a process cannot hold a NUL byte).
COMMIT_SEPARATOR, COMMIT_SEPARATOR_FORMAT = "\x1e", "%x1e"
FIELD_SEPARATOR, FIELD_SEPARATOR_FORMAT = "\x00", "%x00"
SHORT_SHA = 7


class CheckError(Exception):
    """A problem in the inputs or in the files they point to."""


def parse_bool(name, value):
    """Returns the boolean of a "true"/"false" input."""
    if value in ("true", "false"):
        return value == "true"
    raise CheckError(f"input {name} must be true or false, got {value!r}")


def tag_name(tag_format, version):
    """Returns the release tag of version under tag_format."""
    return tag_format.replace("{version}", version)


def guidance(base_ref, head_sha, tag):
    """Returns what to do when commits of the pull request predate the release."""
    head = head_sha or "<head of this pull request>"
    return (
        f"How to proceed: release-please reads the history of {base_ref} newest committer date first and stops at "
        f"the commit of the last release ({tag}), so it never reads a commit dated before that commit: the change "
        "would be missing from the changelog and from the next version. Recreate the branch from the current "
        f"{base_ref} with cherry-pick, which gives each commit a new committer date, and open a new pull request "
        f"from it: git fetch origin && git switch -c <new-branch> origin/{base_ref} && git rev-list --reverse "
        f"--no-merges origin/{base_ref}..{head} | git cherry-pick --stdin && git push -u origin <new-branch>. "
        "Do not rebase this branch and do not force-push it; close this pull request once the new one is open."
    )


def evaluate(release_time, commits):
    """Returns the findings of [(sha, committer time, committer date, header)] against release_time."""
    findings = []
    for sha, time, date, header in commits:
        if time <= release_time:
            findings.append(f"commit {sha[:SHORT_SHA]} ({header}) has the committer date {date}, not later than the release commit")
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
    if not isinstance(value, str) or not VERSION_RE.match(value):
        raise CheckError(f"{manifest_file}: the version of {package!r} must be a version string, got {value!r}")
    return value


def run_git(workspace, *args):
    """Runs git in workspace and returns its standard output."""
    result = subprocess.run(
        ["git", "-C", workspace, *args], capture_output=True, text=True, encoding="utf-8", check=False
    )
    if result.returncode != 0:
        raise CheckError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def resolve_commit(git, ref):
    """Returns the commit SHA of ref, or None when ref does not exist."""
    try:
        return git("rev-parse", "--quiet", "--verify", f"{ref}^{{commit}}").strip() or None
    except CheckError:
        return None


def read_commits(git, base, head_sha):
    """Returns [(sha, committer time, committer date, header)] of the commits base..head that are not merges."""
    output = git(
        "log", "--no-merges",
        f"--format=%H{FIELD_SEPARATOR_FORMAT}%ct{FIELD_SEPARATOR_FORMAT}%cI{FIELD_SEPARATOR_FORMAT}%s{COMMIT_SEPARATOR_FORMAT}",
        f"{base}..{head_sha}",
    )
    commits = []
    for record in output.split(COMMIT_SEPARATOR):
        record = record.strip("\n")
        if not record:
            continue
        sha, time, date, header = record.split(FIELD_SEPARATOR, 3)
        commits.append((sha, int(time), date, header))
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
    if not TAG_FORMAT_RE.match(args.tag_format) or args.tag_format.count("{version}") != 1:
        raise CheckError(f"input tag-format must hold \"{{version}}\" once and only tag name characters, got {args.tag_format!r}")
    if not dry_run:
        if not BRANCH_RE.match(args.base_ref):
            raise CheckError(f"input base-ref must be a branch name; run this check on a pull_request event, got {args.base_ref!r}")
        if not SHA_RE.match(args.head_sha):
            raise CheckError("input head-sha must be a full commit SHA; run this check on a pull_request event")
    return dry_run


def read_manifest_file(workspace, manifest_file):
    path = os.path.join(workspace, manifest_file)
    if not os.path.isfile(path):
        raise CheckError(f"input manifest-file: file {manifest_file} not found in the workspace")
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def check(args, git=None):
    """Runs the check; returns the exit status."""
    git = git or (lambda *a: run_git(args.workspace, *a))
    dry_run = load_inputs(args)
    outputs = {"version": "", "tag": "", "release-sha": "", "result": "skip", "findings": 0}

    if dry_run:
        version = manifest_version(read_manifest_file(args.workspace, args.manifest_file), args.manifest_file, args.package)
        tag = tag_name(args.tag_format, version)
        outputs.update(version=version, tag=tag)
        print(f"dry run: version {version} of {args.package!r}, release tag {tag}; no git history read, nothing checked")
        write_outputs(args.github_output, outputs)
        return 0

    base = f"refs/remotes/origin/{args.base_ref}"
    if resolve_commit(git, base) is None:
        raise CheckError(f"{base} not found: check out the repository with its full history (fetch-depth: 0)")
    version = manifest_version(git("show", f"{base}:{args.manifest_file}"), args.manifest_file, args.package)
    tag = tag_name(args.tag_format, version)
    outputs.update(version=version, tag=tag)

    release_sha = resolve_commit(git, f"refs/tags/{tag}")
    if release_sha is None:
        annotate("warning", f"release tag {tag} of version {version} not found: nothing to compare with, the check passes. "
                            "Check tag-format if the package has released before.")
        write_outputs(args.github_output, outputs)
        return 0
    release_time = int(git("log", "-1", "--format=%ct", release_sha).strip())
    release_date = git("log", "-1", "--format=%cI", release_sha).strip()
    print(f"release {tag}: commit {release_sha[:SHORT_SHA]}, committer date {release_date}")

    commits = read_commits(git, base, args.head_sha)
    findings = evaluate(release_time, commits)
    print(f"{len(commits)} commit(s) of the pull request checked against origin/{args.base_ref}, {len(findings)} finding(s)")
    outputs.update({"release-sha": release_sha, "result": "fail" if findings else "pass", "findings": len(findings)})
    write_outputs(args.github_output, outputs)
    for finding in findings:
        annotate("error", f"{finding} {tag} ({release_date})")
    if findings:
        annotate("error", guidance(args.base_ref, args.head_sha, tag))
    return 1 if findings else 0


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest-file", default=".release-please-manifest.json")
    parser.add_argument("--package", default=".")
    parser.add_argument("--tag-format", default="v{version}")
    parser.add_argument("--base-ref", default="")
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
