"""Checks the inputs of wp-plugin-release and builds the plugin archive.

Subcommands:
  validate   checks every input before any build step or write
  check-ui   checks that the UI build produced the expected file
  archive    builds the distribution ZIP and prints its file listing

Values arrive as --name=value arguments; the composer authentication JSON
arrives in the COMPOSER_AUTH environment variable and is never printed.
Step outputs go to the file named by GITHUB_OUTPUT when it is set.

Standard library only. Run with python3 -I.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import re
import stat
import sys
import zipfile

TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
ZIP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
SCOPE_RE = re.compile(r"^@[a-z0-9][a-z0-9._-]{0,213}$")
# Characters refused in a ZIP exclude pattern (kept from the shell
# implementation this component replaces, so that a caller's pattern list
# keeps the same meaning and the same refusals).
UNSAFE_PATTERN_RE = re.compile(r"[\"';&|()`$<>\\\x00-\x1f\x7f]")
MAX_PATTERNS = 500

# Paths never shipped in the archive: version control, CI, tooling,
# sources of the UI build, tests, documentation and release metadata.
DEFAULT_EXCLUDES = (
    ".git/*",
    ".github/*",
    ".cache/*",
    ".claude/*",
    ".aiox-core/*",
    "tests/*",
    "docs/*",
    "docs-site/*",
    "ui/src/*",
    "ui/node_modules/*",
    "node_modules/*",
    "bitbucket-pipelines.yml",
    ".php-cs-fixer.php",
    ".phpstan.neon",
    ".php-rector.php",
    ".phpunit.xml",
    "phpstan.neon",
    "rector.php",
    "auth.json",
    ".release-please-manifest.json",
    "release-please-config.json",
)

# Fixed entry timestamp: the same tree gives the same archive bytes.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


class InputError(Exception):
    """An input or the tree is not acceptable; the message names the input."""


def parse_bool(name, value):
    if value in ("true", "false"):
        return value == "true"
    raise InputError(f"input {name} must be true or false, got {value!r}")


def check_relative_path(name, value, allow_empty=False):
    """A path relative to the workspace: never absolute, never '..'."""
    if value == "":
        if allow_empty:
            return value
        raise InputError(f"input {name} is empty")
    if UNSAFE_PATTERN_RE.search(value) or "\\" in value:
        raise InputError(f"input {name} holds a forbidden character")
    if os.path.isabs(value):
        raise InputError(f"input {name} must be a relative path, got an absolute one")
    parts = [part for part in value.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise InputError(f"input {name} must not contain '..'")
    return value


def check_tag_name(value):
    if not TAG_RE.match(value) or ".." in value:
        raise InputError(
            "input tag-name must match ^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$ "
            f"without '..', got {value!r}"
        )
    return value


def check_zip_name(value):
    if not ZIP_NAME_RE.match(value) or ".." in value:
        raise InputError(
            "input zip-name must match ^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$ "
            f"without '..', got {value!r}"
        )
    return value


def check_pattern(source, pattern):
    if UNSAFE_PATTERN_RE.search(pattern):
        raise InputError(f"{source}: rejected unsafe ZIP exclude pattern {pattern!r}")
    return pattern


def split_patterns(text):
    """Patterns separated by spaces, tabs or new lines."""
    return [check_pattern("input extra-zip-excludes", item) for item in text.split()]


def read_patterns_file(root, relative):
    """One pattern per line; blank lines and lines starting with '#' skipped."""
    if relative == "":
        return []
    check_relative_path("extra-zip-excludes-file", relative)
    full = os.path.join(root, relative)
    if not os.path.isfile(full) or os.path.islink(full):
        raise InputError(
            f"input extra-zip-excludes-file names {relative!r}, "
            "which is not a regular file under path"
        )
    patterns = []
    with open(full, encoding="utf-8") as handle:
        for line in handle.read().splitlines():
            line = line.strip()
            if line == "" or line.startswith("#"):
                continue
            patterns.append(check_pattern(f"file {relative}", line))
    return patterns


def exclude_patterns(root, extra, extra_file):
    patterns = list(DEFAULT_EXCLUDES) + split_patterns(extra) + read_patterns_file(root, extra_file)
    if len(patterns) > MAX_PATTERNS:
        raise InputError(f"more than {MAX_PATTERNS} ZIP exclude patterns")
    return patterns


def credential_values(node, key=""):
    """Every credential string in the COMPOSER_AUTH object.

    The keys (host names, credential types) and the "username" fields are
    not credentials and are left out, so that masking them does not hide
    ordinary words in the log.
    """
    if isinstance(node, dict):
        values = []
        for child_key, child in node.items():
            values.extend(credential_values(child, child_key))
        return values
    if isinstance(node, list):
        values = []
        for child in node:
            values.extend(credential_values(child, key))
        return values
    if isinstance(node, str) and node != "" and key != "username":
        return [node]
    return []


def mask_lines(values):
    """One ::add-mask:: command per non-blank line of each value.

    The runner consumes these lines: they never appear in the log. A
    multi-line value is masked line by line, as the runner requires.
    """
    lines = []
    for value in values:
        lines.extend(line for line in value.splitlines() if line.strip() != "")
    return "".join(f"::add-mask::{line}\n" for line in dict.fromkeys(lines))


def check_composer_auth(text):
    """COMPOSER_AUTH, when set, is a JSON object. Its value is never printed.

    The runner masks the secret as one whole string only; each credential
    inside the object is masked on its own here, before any build step.
    """
    if text == "":
        return False
    try:
        value = json.loads(text)
    except ValueError:
        raise InputError("secret composer-auth is not valid JSON (content not shown)") from None
    if not isinstance(value, dict):
        raise InputError("secret composer-auth must be a JSON object (content not shown)")
    sys.stdout.write(mask_lines(credential_values(value)))
    sys.stdout.flush()
    return True


def write_outputs(values):
    lines = "".join(f"{key}={value}\n" for key, value in values.items())
    target = os.environ.get("GITHUB_OUTPUT", "")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(lines)
    sys.stdout.write(lines)


def is_excluded(relative, patterns):
    return any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns)


def is_pruned(relative_dir, patterns):
    """True when every path under the directory is excluded.

    A pattern ending in '*' that matches '<dir>/' matches every longer path
    under it too, because '*' also matches '/'.
    """
    probe = relative_dir + "/"
    return any(pattern.endswith("*") and fnmatch.fnmatchcase(probe, pattern) for pattern in patterns)


def inside(root, target):
    root = os.path.realpath(root)
    target = os.path.realpath(target)
    return target == root or target.startswith(root + os.sep)


def collect_files(root, patterns):
    """Sorted (relative path, full path) of every file that goes in the archive."""
    selected = []
    for current, dirs, files in os.walk(root, followlinks=False):
        relative_dir = os.path.relpath(current, root)
        relative_dir = "" if relative_dir == "." else relative_dir.replace(os.sep, "/")
        kept = []
        for name in sorted(dirs):
            relative = f"{relative_dir}/{name}" if relative_dir else name
            full = os.path.join(current, name)
            if is_pruned(relative, patterns):
                continue
            if os.path.islink(full):
                raise InputError(f"{relative} is a symbolic link to a directory; the archive takes regular files only")
            kept.append(name)
        dirs[:] = kept
        for name in sorted(files):
            relative = f"{relative_dir}/{name}" if relative_dir else name
            if is_excluded(relative, patterns):
                continue
            full = os.path.join(current, name)
            if os.path.islink(full) and not inside(root, full):
                raise InputError(f"{relative} is a symbolic link out of path")
            try:
                mode = os.stat(full).st_mode
            except OSError:
                raise InputError(f"{relative} cannot be read (broken symbolic link?)") from None
            if not stat.S_ISREG(mode):
                raise InputError(f"{relative} is not a regular file")
            selected.append((relative, full))
    selected.sort()
    return selected


def write_archive(files, archive_path):
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, full in files:
            info = zipfile.ZipInfo(relative, date_time=ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = os.stat(full).st_mode
            info.external_attr = (stat.S_IFREG | (stat.S_IMODE(mode) & 0o755)) << 16
            with open(full, "rb") as handle:
                archive.writestr(info, handle.read())


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def listing_of(files):
    return "".join(f"{relative}\n" for relative, _ in files)


def command_validate(args):
    # First: the credentials inside composer-auth are masked before any
    # other message of this run.
    check_composer_auth(os.environ.get("COMPOSER_AUTH", ""))
    root = check_relative_path("path", args.path)
    tag = check_tag_name(args.tag_name)
    zip_name = check_zip_name(args.zip_name)
    has_ui = parse_bool("has-ui", args.has_ui)
    parse_bool("has-strauss", args.has_strauss)
    dry_run = parse_bool("dry-run", args.dry_run)
    has_token_inputs = parse_bool("has-app-credentials", args.has_app_credentials)
    if not os.path.isdir(root):
        raise InputError(f"input path names {root!r}, which is not a directory of the workspace")
    if args.npm_github_packages_scope and not SCOPE_RE.match(args.npm_github_packages_scope):
        raise InputError("input npm-github-packages-scope must be an npm scope such as @example")
    if args.npm_github_packages_scope and not has_ui:
        raise InputError("input npm-github-packages-scope is set but has-ui is false")
    if has_ui:
        check_relative_path("ui-check-file", args.ui_check_file)
        if not os.path.isfile(os.path.join(root, "ui", "package.json")):
            raise InputError("input has-ui is true but path has no ui/package.json")
    exclude_patterns(root, args.extra_zip_excludes, args.extra_zip_excludes_file)
    if not dry_run and not has_token_inputs:
        raise InputError(
            "inputs app-client-id and app-private-key are required unless dry-run is true "
            "(variable WP_PLUGIN_RELEASE_APP_CLIENT_ID and secret WP_PLUGIN_RELEASE_APP_PRIVATE_KEY "
            "in the reusable workflow)"
        )
    write_outputs({"archive-name": f"{zip_name}-{tag}.zip"})


def command_check_ui(args):
    root = check_relative_path("path", args.path)
    relative = check_relative_path("ui-check-file", args.ui_check_file)
    if not os.path.isfile(os.path.join(root, relative)):
        raise InputError(f"UI build verification failed: {relative} not found under path")
    print(f"UI build produced {relative}")


def command_archive(args):
    root = check_relative_path("path", args.path)
    tag = check_tag_name(args.tag_name)
    zip_name = check_zip_name(args.zip_name)
    patterns = exclude_patterns(root, args.extra_zip_excludes, args.extra_zip_excludes_file)
    files = collect_files(root, patterns)
    if not files:
        raise InputError("the archive would be empty: every file under path is excluded")
    archive_name = f"{zip_name}-{tag}.zip"
    os.makedirs(args.out_dir, exist_ok=True)
    archive_path = os.path.join(args.out_dir, archive_name)
    write_archive(files, archive_path)
    listing = listing_of(files)
    sys.stdout.write(listing)
    write_outputs(
        {
            "archive-name": archive_name,
            "archive-path": archive_path,
            "archive-sha256": sha256_file(archive_path),
            "files": str(len(files)),
            "files-sha256": hashlib.sha256(listing.encode("utf-8")).hexdigest(),
        }
    )


def build_parser():
    parser = argparse.ArgumentParser(prog="wp_plugin_release.py")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate")
    validate.add_argument("--path", required=True)
    validate.add_argument("--tag-name", required=True)
    validate.add_argument("--zip-name", required=True)
    validate.add_argument("--has-ui", required=True)
    validate.add_argument("--has-strauss", required=True)
    validate.add_argument("--ui-check-file", default="")
    validate.add_argument("--npm-github-packages-scope", default="")
    validate.add_argument("--extra-zip-excludes", default="")
    validate.add_argument("--extra-zip-excludes-file", default="")
    validate.add_argument("--dry-run", required=True)
    validate.add_argument("--has-app-credentials", required=True)

    check_ui = commands.add_parser("check-ui")
    check_ui.add_argument("--path", required=True)
    check_ui.add_argument("--ui-check-file", required=True)

    archive = commands.add_parser("archive")
    archive.add_argument("--path", required=True)
    archive.add_argument("--tag-name", required=True)
    archive.add_argument("--zip-name", required=True)
    archive.add_argument("--extra-zip-excludes", default="")
    archive.add_argument("--extra-zip-excludes-file", default="")
    archive.add_argument("--out-dir", required=True)
    return parser


COMMANDS = {"validate": command_validate, "check-ui": command_check_ui, "archive": command_archive}


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        COMMANDS[args.command](args)
    except InputError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
