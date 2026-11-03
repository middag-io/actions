"""Packages a Moodle plugin working tree as an installable ZIP.

Subcommands (called by action.yml, standard library only):

  check       validates the inputs and version.php, plans the run and writes
              the step outputs (version, release, archive name, which
              dependency installs apply).
  build       copies the plugin tree with rsync and the exclude rules, then
              writes a deterministic ZIP and the file listing.
  npm-access  checks that the token reads every dependency of the lockfile
              resolved from the GitHub Packages npm registry, and reports
              every failure at once.

Run as: python3 -I package.py <subcommand> [options]
"""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import zipfile

# Paths that never ship. rsync semantics: a pattern without an inner "/"
# matches at any depth; a trailing "/" matches directories only.
DEFAULT_EXCLUDES = (
    ".git/",
    ".github/",
    ".cache/",
    ".claude/",
    ".aiox-core/",
    "dist/",
    "node_modules/",
    "vendor/bin/.phpunit/",
    "tests/",
    "docs-site/",
    ".php-cs-fixer.cache",
    ".phpunit.cache/",
    "bitbucket-pipelines.yml",
    "auth.json",
    "composer.phar",
)
# The only exclude file whose absence is not an error.
DEFAULT_EXCLUDES_FILE = ".distignore"
GITHUB_NPM_REGISTRY = "https://npm.pkg.github.com"
# Moodle frankenstyle: <plugintype>_<pluginname>.
ZIP_NAME_RE = re.compile(r"^[a-z][a-z0-9]*_[a-z][a-z0-9_]*$")
TAG_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
NPM_SCRIPT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,127}$")
VERSION_RE = re.compile(r"^[ \t]*\$plugin->version[ \t]*=[ \t]*([0-9]{10}(?:\.[0-9]+)?)[ \t]*;", re.MULTILINE)
QUOTED_FIELD = r"^[ \t]*\$plugin->{name}[ \t]*=[ \t]*(['\"])(.*?)\1[ \t]*;"
# Fixed ZIP entry time: the archive bytes depend on the content only.
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)
EXECUTABLE_MODE = 0o100755
REGULAR_MODE = 0o100644


class InputError(Exception):
    """A value given to the component is invalid."""


def fail(message):
    print(f"::error::{message}", file=sys.stderr)
    return 1


def parse_bool(name, value):
    if value in ("true", "false"):
        return value == "true"
    raise InputError(f"input {name} must be true or false, got {value!r}")


def quoted_field(text, name):
    match = re.search(QUOTED_FIELD.format(name=name), text, re.MULTILINE)
    return match.group(2) if match else ""


def parse_version_file(text):
    """Returns {"version", "release", "component"} read from version.php.

    The file is read, never executed. version and release are required (a
    release build names the version it ships); component, when set, is
    returned so that the caller can compare it with the archive name.
    """
    match = VERSION_RE.search(text)
    if not match:
        raise InputError("version.php must set $plugin->version to a YYYYMMDDXX number")
    release = quoted_field(text, "release")
    if not release.strip():
        raise InputError("version.php must set $plugin->release to a non-empty string")
    return {"version": match.group(1), "release": release, "component": quoted_field(text, "component")}


def relative_path(base, value, name):
    """Resolves value inside base; refuses absolute paths, ".." and links out of base."""
    if not value or os.path.isabs(value) or ".." in value.replace("\\", "/").split("/"):
        raise InputError(f"input {name} must be a relative path inside the plugin tree, got {value!r}")
    base_real = os.path.realpath(base)
    resolved = os.path.realpath(os.path.join(base, value))
    if resolved != base_real and not resolved.startswith(base_real + os.sep):
        raise InputError(f"input {name} resolves outside the plugin tree: {value!r}")
    return resolved


def validate_names(zip_name, tag_name, sha):
    if not ZIP_NAME_RE.match(zip_name):
        raise InputError(f"input zip-name must be a Moodle frankenstyle name such as local_example, got {zip_name!r}")
    if tag_name and not TAG_NAME_RE.match(tag_name):
        raise InputError(f"input tag-name must match {TAG_NAME_RE.pattern}, got {tag_name!r}")
    if not tag_name and not SHA_RE.match(sha):
        raise InputError("without tag-name the archive is named after the commit, and the commit SHA is missing")


def archive_name(zip_name, tag_name, sha):
    suffix = tag_name if tag_name else sha[:8]
    return f"{zip_name}-{suffix}.zip"


def resolve_excludes_file(base, value):
    """Returns the exclude file path, or "" when none applies."""
    if not value:
        return ""
    path = relative_path(base, value, "dist-excludes-file")
    if os.path.isfile(path):
        return path
    if value == DEFAULT_EXCLUDES_FILE:
        return ""
    raise InputError(f"input dist-excludes-file names a file that does not exist: {value!r}")


def plan(args):
    """Validates every input and returns the outputs of the check step."""
    base = args.path or "."
    if base != ".":
        relative_path(".", base, "path")
    if not os.path.isdir(base):
        raise InputError(f"input path is not a directory: {base!r}")
    validate_names(args.zip_name, args.tag_name, args.sha)
    upload_release_asset = parse_bool("upload-release-asset", args.upload_release_asset)
    dry_run = parse_bool("dry-run", args.dry_run)
    if upload_release_asset and not args.tag_name:
        raise InputError("input upload-release-asset needs tag-name: the asset goes to the release of that tag")

    version_file = os.path.join(base, "version.php")
    if not os.path.isfile(version_file):
        raise InputError("version.php not found at the root of the plugin tree; a Moodle plugin needs one")
    with open(version_file, encoding="utf-8") as handle:
        info = parse_version_file(handle.read())
    if info["component"] and info["component"] != args.zip_name:
        raise InputError(f"version.php sets $plugin->component to {info['component']!r}, but zip-name is {args.zip_name!r}")

    has_npm = bool(args.npm_build_command)
    if has_npm:
        if not NPM_SCRIPT_RE.match(args.npm_build_command):
            raise InputError(f"input npm-build-command must be an npm script name, got {args.npm_build_command!r}")
        npm_dir = relative_path(base, args.npm_working_dir, "npm-working-dir")
        if not os.path.isfile(os.path.join(npm_dir, "package.json")):
            raise InputError(f"no package.json in npm-working-dir {args.npm_working_dir!r}")

    return {
        "version": info["version"],
        "release": info["release"],
        "archive-name": archive_name(args.zip_name, args.tag_name, args.sha),
        "excludes-file": resolve_excludes_file(base, args.dist_excludes_file),
        "has-composer": str(os.path.isfile(os.path.join(base, "composer.json"))).lower(),
        "has-npm": str(has_npm).lower(),
        "upload-release-asset": str(upload_release_asset).lower(),
        "dry-run": str(dry_run).lower(),
    }


def write_outputs(outputs):
    for key, value in outputs.items():
        if "\n" in value or "\r" in value:
            raise InputError(f"output {key} holds a line break")
    target = os.environ.get("GITHUB_OUTPUT")
    lines = "".join(f"{key}={value}\n" for key, value in outputs.items())
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(lines)
    print(lines, end="")


def rsync_command(source, destination, excludes_file):
    command = ["rsync", "-a", "--copy-links"]
    for pattern in DEFAULT_EXCLUDES:
        command += ["--exclude", pattern]
    if excludes_file:
        command += ["--exclude-from", excludes_file]
    return command + [source.rstrip("/") + "/", destination.rstrip("/") + "/"]


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def list_files(root):
    """Returns the sorted relative paths of the regular files under root."""
    found = []
    for directory, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(directory, name)
            if not stat.S_ISREG(os.lstat(path).st_mode):
                raise InputError(f"not a regular file after the copy: {os.path.relpath(path, root)}")
            found.append(os.path.relpath(path, root).replace(os.sep, "/"))
    return sorted(found)


def write_zip(package_root, zip_name, files, archive_path):
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in files:
            path = os.path.join(package_root, zip_name, relative)
            entry = zipfile.ZipInfo(f"{zip_name}/{relative}", date_time=ZIP_DATE_TIME)
            entry.create_system = 3
            mode = EXECUTABLE_MODE if os.stat(path).st_mode & 0o111 else REGULAR_MODE
            entry.external_attr = mode << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            with open(path, "rb") as handle:
                archive.writestr(entry, handle.read())


def build(args):
    """Copies the tree, writes the ZIP and the listing; returns the outputs."""
    if os.path.exists(args.out_dir) and os.listdir(args.out_dir):
        raise InputError(f"output directory is not empty: {args.out_dir}")
    package_root = os.path.join(args.out_dir, "package")
    destination = os.path.join(package_root, args.zip_name)
    os.makedirs(destination)
    subprocess.run(rsync_command(args.path or ".", destination, args.excludes_file), check=True)

    files = list_files(destination)
    if not files:
        raise InputError("the package is empty after the exclude rules")
    listing = "".join(f"{file_sha256(os.path.join(destination, f))}  {args.zip_name}/{f}\n" for f in files)
    listing_path = os.path.join(args.out_dir, "listing.sha256")
    with open(listing_path, "w", encoding="utf-8") as handle:
        handle.write(listing)
    archive_path = os.path.join(args.out_dir, args.archive_name)
    write_zip(package_root, args.zip_name, files, archive_path)

    print(listing, end="")
    return {
        "archive-path": archive_path,
        "files": str(len(files)),
        "listing-sha256": hashlib.sha256(listing.encode("utf-8")).hexdigest(),
    }


def walk_dependencies(node, prefix=""):
    """Yields (name, resolved) from a lockfile v1 dependencies tree."""
    for name, entry in (node.get("dependencies") or {}).items():
        if isinstance(entry, dict):
            yield name, entry.get("resolved", "")
            yield from walk_dependencies(entry)


def github_registry_packages(lock_text):
    """Returns the sorted package names that the lockfile resolves from GitHub Packages."""
    lock = json.loads(lock_text)
    pairs = []
    for key, entry in (lock.get("packages") or {}).items():
        if key and isinstance(entry, dict) and "node_modules/" in key:
            pairs.append((key.rsplit("node_modules/", 1)[1], entry.get("resolved", "")))
    if not pairs:
        pairs = list(walk_dependencies(lock))
    return sorted({name for name, resolved in pairs if str(resolved).startswith(GITHUB_NPM_REGISTRY + "/")})


def npm_access(args):
    lock_path = os.path.join(args.working_dir, "package-lock.json")
    if not os.path.isfile(lock_path):
        print("no package-lock.json: nothing to check")
        return 0
    with open(lock_path, encoding="utf-8") as handle:
        packages = github_registry_packages(handle.read())
    if not packages:
        print(f"no dependency resolved from {GITHUB_NPM_REGISTRY}: nothing to check")
        return 0
    failed = []
    for name in packages:
        result = subprocess.run(
            ["npm", "view", name, "version", f"--registry={GITHUB_NPM_REGISTRY}"],
            cwd=args.working_dir, capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            print(f"ok    {name} -> {result.stdout.strip()}")
        else:
            print(f"FAIL  {name}")
            failed.append(name)
    if failed:
        return fail(
            "the token cannot read: " + " ".join(failed)
            + ". Give the calling repository read access to each package, and give the token packages: read."
        )
    return 0


def parser():
    root = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = root.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.add_argument("--path", default=".")
    check.add_argument("--zip-name", required=True)
    check.add_argument("--tag-name", default="")
    check.add_argument("--sha", default="")
    check.add_argument("--dist-excludes-file", default="")
    check.add_argument("--npm-build-command", default="")
    check.add_argument("--npm-working-dir", default=".")
    check.add_argument("--upload-release-asset", default="false")
    check.add_argument("--dry-run", default="false")
    build_cmd = commands.add_parser("build")
    build_cmd.add_argument("--path", default=".")
    build_cmd.add_argument("--zip-name", required=True)
    build_cmd.add_argument("--archive-name", required=True)
    build_cmd.add_argument("--excludes-file", default="")
    build_cmd.add_argument("--out-dir", required=True)
    access = commands.add_parser("npm-access")
    access.add_argument("--working-dir", default=".")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "check":
            outputs = plan(args)
            write_outputs(outputs)
            if outputs["dry-run"] == "true":
                print("dry run: no dependency install, no artifact upload, no release upload")
            return 0
        if args.command == "build":
            write_outputs(build(args))
            return 0
        return npm_access(args)
    except InputError as error:
        return fail(str(error))
    except subprocess.CalledProcessError as error:
        return fail(f"{error.cmd[0]} failed with exit status {error.returncode}")


if __name__ == "__main__":
    sys.exit(main())
