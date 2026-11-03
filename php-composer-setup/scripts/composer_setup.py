"""Checks the inputs, writes Composer credentials and installs dependencies.

Called by action.yml of the php-composer-setup component. Standard library
only; run as ``python3 -I``. Subcommands:

- ``check``: validates every input before any write and writes the plan
  (``auth-entries``, ``install-command``) to ``$GITHUB_OUTPUT``.
- ``auth``: merges the HTTP Basic and GitHub credentials into the global
  Composer ``auth.json``. Values come from the environment, never from the
  command line.
- ``install``: runs ``composer install`` in the working directory.

Secrets are never printed: the ``auth`` subcommand masks them first.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

PHP_VERSION_RE = re.compile(r"^(?:[0-9]+\.[0-9]+|latest|nightly|pre-installed)$")
PHP_EXTENSIONS_RE = re.compile(r"^[A-Za-z0-9_.:, -]*$")
COVERAGE_VALUES = ("none", "xdebug", "xdebug2", "pcov")
HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252}[A-Za-z0-9])?(?::[0-9]{1,5})?$")
USERNAME_RE = re.compile(r"^[^\s:]{1,256}$")
BOOLEAN_VALUES = ("true", "false")
GITHUB_HOST = "github.com"
AUTH_FILE_MODE = 0o600
MERGED_AUTH_KEYS = ("http-basic", "github-oauth")


class InputError(ValueError):
    """An input is invalid; the message names the input."""


def parse_boolean(name, value):
    if value not in BOOLEAN_VALUES:
        raise InputError(f"{name} must be true or false, got {value!r}")
    return value == "true"


def check_working_directory(value, root):
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise InputError(f"working-directory must be a relative path inside the checkout, got {value!r}")
    directory = Path(root) / path
    if not directory.is_dir():
        raise InputError(f"working-directory {value!r} is not a directory")
    if not (directory / "composer.json").is_file():
        raise InputError(f"working-directory {value!r} has no composer.json")
    return directory


def check_http_basic(host, username, has_password, dry_run):
    if not host and not username:
        if has_password:
            raise InputError("http-basic-password is set but http-basic-host and http-basic-username are empty")
        return None
    if not host or not username:
        raise InputError("http-basic-host and http-basic-username go together; set both or neither")
    if not HOST_RE.match(host):
        raise InputError(f"http-basic-host must be a host name with an optional port, got {host!r}")
    if not USERNAME_RE.match(username):
        raise InputError("http-basic-username must be 1 to 256 characters without spaces or colons")
    if not has_password and not dry_run:
        raise InputError(
            "http-basic-password is empty: pass it from a secret, or set dry-run: true "
            "(reusable workflow: the secret http-basic-password, from PHP_COMPOSER_SETUP_HTTP_BASIC_PASSWORD)"
        )
    return host


def install_command(no_dev, optimize_autoloader):
    command = ["composer", "install", "--no-interaction", "--prefer-dist"]
    if no_dev:
        command.append("--no-dev")
    if optimize_autoloader:
        command.append("--optimize-autoloader")
    return command


def auth_entries(http_basic_host, has_github_token):
    entries = []
    if http_basic_host:
        entries.append(f"http-basic:{http_basic_host}")
    if has_github_token:
        entries.append(f"github-oauth:{GITHUB_HOST}")
    return entries


def build_plan(args, root):
    """Validates every input. Returns the outputs of the check step."""
    if not PHP_VERSION_RE.match(args.php_version):
        raise InputError(f"php-version must look like 8.4, latest, nightly or pre-installed, got {args.php_version!r}")
    if not PHP_EXTENSIONS_RE.match(args.php_extensions):
        raise InputError("php-extensions holds characters other than letters, digits, '_', '.', ':', ',', '-' and spaces")
    if args.coverage not in COVERAGE_VALUES:
        raise InputError(f"coverage must be one of {', '.join(COVERAGE_VALUES)}, got {args.coverage!r}")
    dry_run = parse_boolean("dry-run", args.dry_run)
    install = parse_boolean("install", args.install)
    no_dev = parse_boolean("no-dev", args.no_dev)
    optimize = parse_boolean("optimize-autoloader", args.optimize_autoloader)
    has_password = parse_boolean("has-http-basic-password", args.has_http_basic_password)
    has_github_token = parse_boolean("has-github-token", args.has_github_token)
    check_working_directory(args.working_directory, root)
    host = check_http_basic(args.http_basic_host, args.http_basic_username, has_password, dry_run)
    command = install_command(no_dev, optimize) if install else []
    return {
        "auth-entries": ",".join(auth_entries(host, has_github_token)),
        "install-command": " ".join(command),
    }


def write_outputs(outputs):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


def merge_auth(existing, host, username, password, github_token):
    """Returns a new auth.json mapping; the existing one is left unchanged."""
    merged = {key: dict(value) if isinstance(value, dict) else value for key, value in existing.items()}
    if host:
        merged["http-basic"] = {**merged.get("http-basic", {}), host: {"username": username, "password": password}}
    if github_token:
        merged["github-oauth"] = {**merged.get("github-oauth", {}), GITHUB_HOST: github_token}
    return merged


def read_auth_file(path):
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise InputError(f"the existing Composer auth.json is not valid JSON (line {error.lineno})") from None
    if not isinstance(data, dict):
        raise InputError("the existing Composer auth.json is not a JSON object")
    for key in MERGED_AUTH_KEYS:
        if key in data and not isinstance(data[key], dict):
            raise InputError(f"the {key} entry of the existing Composer auth.json is not a JSON object")
    return data


def write_auth_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, AUTH_FILE_MODE)
    # O_CREAT applies the mode only to a new file: a leftover temporary file
    # keeps its own mode, so set it before any credential is written.
    os.fchmod(descriptor, AUTH_FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def composer_home():
    result = subprocess.run(
        ["composer", "config", "--global", "home"],
        check=True,
        capture_output=True,
        text=True,
    )
    home = result.stdout.strip()
    if not home:
        raise InputError("composer config --global home returned an empty path")
    return Path(home)


def mask(value):
    for line in value.splitlines():
        if line:
            print(f"::add-mask::{line}", flush=True)


def run_auth(args):
    host = os.environ.get("HTTP_BASIC_HOST", "")
    username = os.environ.get("HTTP_BASIC_USERNAME", "")
    password = os.environ.get("HTTP_BASIC_PASSWORD", "")
    github_token = os.environ.get("COMPOSER_GITHUB_TOKEN", "")
    mask(password)
    mask(github_token)
    if not host and not github_token:
        print("No Composer credentials to configure.")
        return
    path = (Path(args.composer_home) if args.composer_home else composer_home()) / "auth.json"
    merged = merge_auth(read_auth_file(path), host, username, password, github_token)
    write_auth_file(path, merged)
    print(f"Configured Composer credentials: {', '.join(auth_entries(host, bool(github_token)))}.")


def run_install(args):
    command = install_command(
        parse_boolean("no-dev", args.no_dev),
        parse_boolean("optimize-autoloader", args.optimize_autoloader),
    )
    print(f"Running: {' '.join(command)}", flush=True)
    return subprocess.run(command, cwd=args.working_directory, check=False).returncode


def run_check(args):
    outputs = build_plan(args, Path.cwd())
    mode = "Dry run: would" if args.dry_run == "true" else "Will"
    entries = outputs["auth-entries"] or "none"
    command = outputs["install-command"] or "no install"
    print(f"{mode} set up PHP {args.php_version} ({args.php_extensions or 'no extra extensions'}, coverage {args.coverage}).")
    print(f"{mode} configure Composer credentials: {entries}.")
    print(f"{mode} run in {args.working_directory}: {command}.")
    write_outputs(outputs)


def parser():
    root = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = root.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.add_argument("--php-version", required=True)
    check.add_argument("--php-extensions", default="")
    check.add_argument("--coverage", default="none")
    check.add_argument("--working-directory", default=".")
    check.add_argument("--http-basic-host", default="")
    check.add_argument("--http-basic-username", default="")
    check.add_argument("--has-http-basic-password", default="false")
    check.add_argument("--has-github-token", default="false")
    check.add_argument("--install", default="true")
    check.add_argument("--no-dev", default="false")
    check.add_argument("--optimize-autoloader", default="false")
    check.add_argument("--dry-run", default="false")
    auth = commands.add_parser("auth")
    auth.add_argument("--composer-home", default="")
    install = commands.add_parser("install")
    install.add_argument("--working-directory", default=".")
    install.add_argument("--no-dev", default="false")
    install.add_argument("--optimize-autoloader", default="false")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "check":
            run_check(args)
            return 0
        if args.command == "auth":
            run_auth(args)
            return 0
        return run_install(args)
    except InputError as error:
        print(f"::error::{error}", flush=True)
        return 1
    except subprocess.CalledProcessError as error:
        print(f"::error::{error.cmd[0]} exited with {error.returncode}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
