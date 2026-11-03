#!/usr/bin/env python3
"""Plans a Cloudflare Pages deploy of a static site.

Validates the inputs, computes the content hash of the site sources and
compares it with the hash that the last deploy wrote to
<deploy-url>/.content-hash. Writes the plan to $GITHUB_OUTPUT:

  content-hash     SHA-256 of the site sources (see content_hash)
  deploy           true when the build and the deploy must run
  build-directory  directory where the build command runs
  dist-path        built site directory that is deployed
  deploy-url       URL that holds the hash of the deployed site
  command          Wrangler command of the deploy

In a dry run, nothing is fetched and deploy is false: the plan is printed
only. No credential is read: the caller states whether the API token is
set, never its value.

Standard library only; run with python3 -I.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

VARIABLE_PREFIX = "CLOUDFLARE_PAGES_DEPLOY_"
HASH_FILE = ".content-hash"
DEFAULT_DIST_SUFFIX = ".vitepress/dist"
# Directories under the site path that never count for the content hash:
# dependencies, version control and the build outputs of VitePress.
EXCLUDED_DIR_NAMES = frozenset({"node_modules", ".git"})
EXCLUDED_SUFFIXES = (".vitepress/dist", ".vitepress/cache")
FETCH_TIMEOUT_SECONDS = 15
FETCH_MAX_BYTES = 256
# Cloudflare answers 403 to the default "Python-urllib/3.x" user agent, which
# would make every run deploy. The fetch names this component instead.
USER_AGENT = "cloudflare-pages-deploy"

# A path input: relative, no "..", only these characters. The paths end up
# in a Wrangler command line, so no whitespace and no shell metacharacter.
PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
# Project name accepted by this component: it is also the <name>.pages.dev
# subdomain, so lowercase letters, digits and inner dashes only.
PROJECT_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,56}[a-z0-9])?$")
ACCOUNT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
DEPLOY_URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~/-]*)?$")
NODE_VERSION_RE = re.compile(r"^[A-Za-z0-9.*/-]+$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class InputError(ValueError):
    """An input is missing or invalid."""


@dataclass(frozen=True)
class Plan:
    content_hash: str
    deploy: bool
    build_directory: str
    dist_path: str
    deploy_url: str
    command: str

    def outputs(self) -> list[tuple[str, str]]:
        return [
            ("content-hash", self.content_hash),
            ("deploy", "true" if self.deploy else "false"),
            ("build-directory", self.build_directory),
            ("dist-path", self.dist_path),
            ("deploy-url", self.deploy_url),
            ("command", self.command),
        ]


def parse_bool(name: str, value: str) -> bool:
    if value in ("true", "false"):
        return value == "true"
    raise InputError(f"input {name} must be true or false, got {value!r}")


def missing(name: str, with_variable: bool) -> InputError:
    variable = VARIABLE_PREFIX + name.upper().replace("-", "_")
    hint = f" or the variable {variable}" if with_variable else ""
    return InputError(f"input {name} is required; set it in with:{hint}")


def check_path(name: str, value: str) -> str:
    """Returns the normalized relative path, or raises InputError."""
    if not PATH_RE.match(value):
        raise InputError(f"input {name} has characters outside [A-Za-z0-9._/-]: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise InputError(f"input {name} must be a relative path inside the repository: {value!r}")
    return str(path)


def check_project_name(value: str) -> str:
    if not value:
        raise missing("project-name", with_variable=True)
    if not PROJECT_NAME_RE.match(value):
        raise InputError(
            f"input project-name must be 1 to 58 lowercase letters, digits or dashes: {value!r}"
        )
    return value


def check_account_id(value: str, dry_run: bool) -> None:
    if not value:
        if dry_run:
            return
        raise missing("account-id", with_variable=True)
    if not ACCOUNT_ID_RE.match(value):
        raise InputError("input account-id must be 32 lowercase hexadecimal characters")


def resolve_deploy_url(value: str, project_name: str) -> str:
    url = value.rstrip("/") if value else f"https://{project_name}.pages.dev"
    if not DEPLOY_URL_RE.match(url):
        raise InputError(f"input deploy-url must be an https URL without query or fragment: {value!r}")
    return url


def check_build(build_command: str, node_version: str) -> None:
    if not build_command.strip():
        raise InputError("input build-command must not be empty")
    if "\n" in build_command or "\r" in build_command:
        raise InputError("input build-command must be a single line")
    if not NODE_VERSION_RE.match(node_version):
        raise InputError(f"input node-version is not a version: {node_version!r}")


def is_excluded_dir(relative: PurePosixPath, excluded: set[str]) -> bool:
    text = str(relative)
    return (
        relative.name in EXCLUDED_DIR_NAMES
        or text in excluded
        or any(text == suffix or text.endswith("/" + suffix) for suffix in EXCLUDED_SUFFIXES)
    )


def file_digest(path: Path) -> str:
    if path.is_symlink():
        return "link:" + os.readlink(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_site_files(root: Path, excluded: set[str]) -> list[PurePosixPath]:
    files: list[PurePosixPath] = []
    for current, dirs, names in os.walk(root, followlinks=False):
        base = PurePosixPath(Path(current).relative_to(root).as_posix())
        dirs[:] = sorted(d for d in dirs if not is_excluded_dir(base / d, excluded))
        files.extend(base / name for name in names)
    return sorted(files, key=str)


def content_hash(root: Path, excluded: set[str]) -> str:
    """SHA-256 over "<relative path>\\0<file SHA-256>\\n" of every file, by path.

    Every file under the site path counts (sources, theme, assets and the
    build configuration), except the excluded directories.
    """
    digest = hashlib.sha256()
    for relative in list_site_files(root, excluded):
        digest.update(f"{relative}\0{file_digest(root / relative)}\n".encode())
    return digest.hexdigest()


def fetch_deployed_hash(url: str) -> str:
    """Returns the hash published by the last deploy, or "" when none."""
    request = urllib.request.Request(
        f"{url}/{HASH_FILE}",
        headers={"Cache-Control": "no-cache", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            text = response.read(FETCH_MAX_BYTES).decode("ascii", "replace").strip()
    except (urllib.error.URLError, OSError, ValueError) as error:
        print(f"No deployed hash at {url}/{HASH_FILE} ({error}); the site will be deployed.")
        return ""
    return text if HASH_RE.match(text) else ""


def excluded_dist(docs_path: str, dist_path: str) -> set[str]:
    """Returns the dist path relative to the site path, when it is inside it."""
    try:
        return {str(PurePosixPath(dist_path).relative_to(PurePosixPath(docs_path)))}
    except ValueError:
        return set()


def build_plan(args: argparse.Namespace, workspace: Path, fetch: Callable[[str], str]) -> Plan:
    dry_run = parse_bool("dry-run", args.dry_run)
    force = parse_bool("force", args.force)
    if not args.docs_path:
        raise missing("docs-path", with_variable=False)
    docs_path = check_path("docs-path", args.docs_path)
    project_name = check_project_name(args.project_name)
    check_account_id(args.account_id, dry_run)
    if not dry_run and not parse_bool("has-api-token", args.has_api_token):
        raise InputError("secret api-token is required unless dry-run is true")
    check_build(args.build_command, args.node_version)
    deploy_url = resolve_deploy_url(args.deploy_url, project_name)
    build_directory = check_path("build-working-directory", args.build_working_directory or docs_path)
    dist_path = check_path("dist-path", args.dist_path or f"{docs_path}/{DEFAULT_DIST_SUFFIX}")
    if args.cache_dependency_path:
        check_path("cache-dependency-path", args.cache_dependency_path)

    root = workspace / docs_path
    if not root.is_dir():
        raise InputError(f"input docs-path is not a directory of the checkout: {docs_path}")
    local_hash = content_hash(root, excluded_dist(docs_path, dist_path))
    command = f"pages deploy {dist_path} --project-name={project_name}"
    deploy = decide(dry_run, force, local_hash, deploy_url, fetch)
    return Plan(local_hash, deploy, build_directory, dist_path, deploy_url, command)


def decide(dry_run: bool, force: bool, local_hash: str, url: str, fetch: Callable[[str], str]) -> bool:
    if dry_run:
        print(f"Dry run: no fetch of {url}/{HASH_FILE}, no build and no deploy.")
        return False
    if force:
        print("Deploy forced: the deployed hash is not compared.")
        return True
    deployed = fetch(url)
    print(f"Deployed hash: {deployed or '<none>'}")
    print(f"Local hash:    {local_hash}")
    if deployed == local_hash:
        print("Hashes match: the deploy is skipped.")
        return False
    print("Hashes differ: the site will be built and deployed.")
    return True


def write_outputs(plan: Plan, output_file: str) -> None:
    lines = "".join(f"{key}={value}\n" for key, value in plan.outputs())
    print(lines, end="")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as handle:
            handle.write(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name, default in (
        ("docs-path", ""),
        ("project-name", ""),
        ("deploy-url", ""),
        ("account-id", ""),
        ("node-version", "22"),
        ("build-command", "npm run docs:build"),
        ("build-working-directory", ""),
        ("dist-path", ""),
        ("cache-dependency-path", ""),
        ("force", "false"),
        ("dry-run", "false"),
        ("has-api-token", "false"),
    ):
        parser.add_argument(f"--{name}", default=default)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    try:
        plan = build_plan(parse_args(argv), Path.cwd(), fetch_deployed_hash)
    except InputError as error:
        print(f"::error::{error}")
        return 1
    write_outputs(plan, os.environ.get("GITHUB_OUTPUT", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
