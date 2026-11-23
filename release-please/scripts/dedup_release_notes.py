"""Drops merge-commit duplicates from release-please release notes.

release-please splits the body of a GitHub default merge commit
("Merge pull request #N from x", a blank line, "fix(scope): subject") into
a changelog entry of its own that carries the sha of the merge commit. The
original commit has the same subject, so the notes list the change twice.

In the top version section of each CHANGELOG.md, and in each version
section of a release pull request body, an entry is dropped only when both
hold: its sha is a merge commit (two or more parents), and another entry of
the same section with the same subject is not a merge commit. The entry of
the original commit is kept. Two ordinary commits with the same subject are
both kept.

Without --check the files are rewritten in place, and only when something
was dropped (a second run changes nothing). With --check nothing is
written: the script reports the duplicates that remain and exits non-zero.

Exit status: 0 when the run succeeds (--check: no duplicate remains), 1
otherwise. Standard library only. Run: python3 -I dedup_release_notes.py --help
"""

import argparse
import json
import os
import re
import subprocess
import sys

# A changelog entry that ends with the link of one commit, as release-please
# writes it: "* [**scope:** ]subject ([abc1234](https://host/o/r/commit/<sha>))".
ENTRY_RE = re.compile(r"^(?P<lead>\s*[*-] )(?P<subject>.+?) \(\[(?P<sha>[0-9a-f]{7,40})\]\([^)\s]+\)\)\s*$")
VERSION_HEADING_RE = re.compile(r"^## ")
GROUP_HEADING_RE = re.compile(r"^###+ ")
# What can follow the last entry of a group: the next heading or the end of a
# version section in a release pull request body.
SECTION_END_RE = re.compile(r"^(#{1,6} |---\s*$|</details>)")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
# A repository-relative path: no absolute path, no "..", no empty segment.
PATH_RE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/-]+$")
DEFAULT_CHANGELOG = "CHANGELOG.md"


class DedupError(Exception):
    """A problem in the inputs, or a commit that cannot be looked up."""


def git_is_merge(repo_dir):
    """Returns a function sha -> bool: True when the commit has 2+ parents."""
    cache = {}

    def is_merge(sha):
        if sha not in cache:
            if not SHA_RE.match(sha):
                raise DedupError(f"not a commit sha: {sha!r}")
            result = subprocess.run(
                ["git", "-C", repo_dir, "rev-list", "--parents", "-n", "1", sha, "--"],
                capture_output=True,
                text=True,
                check=False,
            )
            fields = result.stdout.split()
            if result.returncode != 0 or not fields:
                raise DedupError(
                    f"commit {sha} not found in {repo_dir}: check out the full history of the release branch"
                    f" ({result.stderr.strip()})"
                )
            cache[sha] = len(fields) > 2
        return cache[sha]

    return is_merge


def split_sections(lines):
    """Splits lines into (head, [version section, ...]); a section starts at "## "."""
    starts = [index for index, line in enumerate(lines) if VERSION_HEADING_RE.match(line)]
    if not starts:
        return lines, []
    bounds = starts + [len(lines)]
    return lines[: starts[0]], [lines[bounds[i] : bounds[i + 1]] for i in range(len(starts))]


def find_duplicates(section, is_merge):
    """Returns the indexes of the merge-commit entries of a section to drop."""
    entries = {}
    for index, line in enumerate(section):
        match = ENTRY_RE.match(line)
        if match:
            entries.setdefault(match.group("subject"), []).append((index, match.group("sha")))
    drop = []
    for shared in (group for group in entries.values() if len(group) > 1):
        merge_flags = [(index, is_merge(sha)) for index, sha in shared]
        if any(not merge for _, merge in merge_flags):
            drop.extend(index for index, merge in merge_flags if merge)
    return sorted(drop)


def remove_empty_groups(lines):
    """Removes a "### Group" heading left without entries, with its blank lines."""
    result = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if GROUP_HEADING_RE.match(line):
            ahead = index + 1
            while ahead < len(lines) and not lines[ahead].strip():
                ahead += 1
            if ahead >= len(lines) or SECTION_END_RE.match(lines[ahead]):
                index = ahead
                continue
        result.append(line)
        index += 1
    return result


def dedup_section(section, is_merge):
    """Returns (new section lines, dropped entry lines)."""
    drop = find_duplicates(section, is_merge)
    if not drop:
        return section, []
    dropped_indexes = set(drop)
    kept = [line for index, line in enumerate(section) if index not in dropped_indexes]
    return remove_empty_groups(kept), [section[index] for index in drop]


def dedup_text(text, is_merge, top_only):
    """Returns (new text, dropped entry lines) for a changelog or a PR body."""
    lines = text.split("\n")
    head, sections = split_sections(lines)
    dropped = []
    result = list(head)
    for number, section in enumerate(sections):
        if top_only and number > 0:
            result.extend(section)
            continue
        new_section, gone = dedup_section(section, is_merge)
        result.extend(new_section)
        dropped.extend(gone)
    return "\n".join(result), dropped


def read_text(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError) as error:
        raise DedupError(f"cannot read {path}: {error}") from error


def changed_files(repo_dir, base_ref):
    """Returns the files that differ between base_ref and HEAD (three-dot diff)."""
    result = subprocess.run(
        ["git", "-C", repo_dir, "diff", "--name-only", f"{base_ref}...HEAD", "--"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise DedupError(f"git diff against {base_ref} failed: {result.stderr.strip()}")
    return set(result.stdout.splitlines())


def changelogs_from_config(repo_dir, config_file, base_ref):
    """Returns the changelog paths of the configured packages the release changes."""
    try:
        config = json.loads(read_text(os.path.join(repo_dir, config_file)))
    except json.JSONDecodeError as error:
        raise DedupError(f"{config_file} is not valid JSON ({error})") from error
    packages = config.get("packages") if isinstance(config, dict) else None
    if not isinstance(packages, dict):
        raise DedupError(f"{config_file} has no packages object")
    paths = []
    for package, options in packages.items():
        name = (options or {}).get("changelog-path", DEFAULT_CHANGELOG)
        path = name if package == "." else f"{package}/{name}"
        if not PATH_RE.match(path):
            raise DedupError(f"changelog path {path!r} of package {package!r} is not a relative path")
        paths.append(path)
    if base_ref:
        changed = changed_files(repo_dir, base_ref)
        paths = [path for path in paths if path in changed]
    return paths


def plan(args, is_merge):
    """Returns [(path, new text, dropped lines)] for every input that changes."""
    paths = []
    for name in args.changelog:
        if not PATH_RE.match(name):
            raise DedupError(f"--changelog must be a relative path, got {name!r}")
        paths.append(os.path.join(args.repo_dir, name))
    if args.config_file:
        paths += [
            os.path.join(args.repo_dir, name)
            for name in changelogs_from_config(args.repo_dir, args.config_file, args.changed_since)
        ]
    work = [(path, True) for path in paths if os.path.isfile(path)]
    if args.body_file:
        work.append((args.body_file, False))
    changes = []
    for path, top_only in work:
        text = read_text(path)
        new_text, dropped = dedup_text(text, is_merge, top_only)
        if dropped:
            changes.append((path, new_text, dropped))
    return changes


def run(args, is_merge):
    changes = plan(args, is_merge)
    for path, _, dropped in changes:
        for line in dropped:
            level = "error" if args.check else "notice"
            verb = "merge-commit duplicate" if args.check else "dropped merge-commit duplicate"
            print(f"::{level} file={path}::{verb}: {line.strip()}")
    if args.check:
        return 1 if changes else 0
    # Every file is computed before the first write, so a lookup failure
    # never leaves a partly edited set of files.
    for path, new_text, _ in changes:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(new_text)
    total = sum(len(dropped) for _, _, dropped in changes)
    print(f"dropped {total} merge-commit duplicate(s) in {len(changes)} file(s)")
    return 0


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-dir", default=".", help="git checkout that holds the release branch")
    parser.add_argument("--changelog", action="append", default=[], help="changelog path relative to --repo-dir")
    parser.add_argument("--config-file", default="", help="release-please config, relative to --repo-dir")
    parser.add_argument("--changed-since", default="", help="with --config-file, keep only changelogs changed since this ref")
    parser.add_argument("--body-file", default="", help="release pull request body file")
    parser.add_argument("--check", action="store_true", help="report only; exit 1 when a duplicate remains")
    return parser.parse_args(argv)


def main(argv=None, is_merge=None):
    args = parse_args(argv)
    try:
        return run(args, is_merge or git_is_merge(args.repo_dir))
    except DedupError as error:
        print(f"::error::{error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
