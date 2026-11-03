"""Checks the inputs of wp-theme-check and plans the checks to run.

Runs before any other step of the composite action. It fails fast, with an
error that names the input, when a value is invalid or missing. It writes the
output `checks` (the enabled checks, separated by spaces, in run order) and
prints the commands the action runs. It never reads the password itself, only
whether one was passed.

Standard library only. Run as: python3 -I check_inputs.py --php-version=8.4 ...
"""

import argparse
import json
import os
import re
import sys

PHP_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+$")
EXTENSIONS_RE = re.compile(r"^[A-Za-z0-9_.:, -]*$")
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(:[0-9]{1,5})?$")
BOOLEANS = ("true", "false")

# Check name, enabling input, command run from the working directory.
CHECKS = (
    ("php-cs-fixer", "has-cs-fixer", "vendor/bin/php-cs-fixer fix --dry-run --diff"),
    ("phpstan", "has-phpstan", "vendor/bin/phpstan analyse"),
    ("rector", "has-rector", "vendor/bin/rector process --dry-run"),
)
INSTALL_COMMAND = "composer install --no-interaction --prefer-dist --no-progress"


class InputError(Exception):
    """An input value that the action rejects."""


def parse_boolean(name, value):
    if value not in BOOLEANS:
        raise InputError(f"input {name} must be true or false, got {value!r}")
    return value == "true"


def check_working_directory(value, workspace):
    if value == "" or os.path.isabs(value):
        raise InputError(f"input working-directory must be a relative path, got {value!r}")
    parts = value.replace("\\", "/").split("/")
    if ".." in parts:
        raise InputError("input working-directory must stay inside the checkout (no '..')")
    directory = os.path.join(workspace, value)
    if not os.path.isdir(directory):
        raise InputError(f"input working-directory: directory {value!r} not found in the checkout")
    composer_json = os.path.join(directory, "composer.json")
    try:
        with open(composer_json, encoding="utf-8") as handle:
            document = json.load(handle)
    except FileNotFoundError:
        raise InputError(f"no composer.json in working-directory {value!r}") from None
    except (OSError, ValueError) as error:
        raise InputError(f"composer.json in working-directory {value!r} is not valid JSON: {error}") from None
    if not isinstance(document, dict):
        raise InputError(f"composer.json in working-directory {value!r} is not a JSON object")


def check_composer_auth(host, username, has_password, dry_run):
    if host == "":
        if username != "" or has_password:
            raise InputError(
                "input composer-auth-host is empty but a username or password was passed: "
                "set composer-auth-host (variable WP_THEME_CHECK_COMPOSER_AUTH_HOST)"
            )
        return
    if not HOST_RE.match(host):
        raise InputError(f"input composer-auth-host must be a host name with an optional port, got {host!r}")
    if username == "":
        raise InputError(
            "input composer-auth-username is empty while composer-auth-host is set "
            "(variable WP_THEME_CHECK_COMPOSER_AUTH_USERNAME)"
        )
    if not has_password and not dry_run:
        raise InputError(
            "input composer-auth-password is empty while composer-auth-host is set: pass it from the "
            "secret WP_THEME_CHECK_COMPOSER_AUTH_PASSWORD or set dry-run: true"
        )


def plan(args, workspace):
    """Validates every input; returns the names of the enabled checks."""
    if not PHP_VERSION_RE.match(args.php_version):
        raise InputError(f"input php-version must look like 8.4, got {args.php_version!r}")
    if not EXTENSIONS_RE.match(args.php_extensions):
        raise InputError(f"input php-extensions has characters outside [A-Za-z0-9_.:, -]: {args.php_extensions!r}")
    flags = {
        "has-cs-fixer": parse_boolean("has-cs-fixer", args.has_cs_fixer),
        "has-phpstan": parse_boolean("has-phpstan", args.has_phpstan),
        "has-rector": parse_boolean("has-rector", args.has_rector),
    }
    dry_run = parse_boolean("dry-run", args.dry_run)
    has_password = parse_boolean("has-composer-auth-password", args.has_composer_auth_password)
    check_working_directory(args.working_directory, workspace)
    check_composer_auth(args.composer_auth_host, args.composer_auth_username, has_password, dry_run)
    checks = [name for name, flag, _ in CHECKS if flags[flag]]
    if not checks:
        raise InputError("every check is disabled: enable has-cs-fixer, has-phpstan or has-rector")
    return checks


def report(checks, args, dry_run):
    prefix = "dry run, would run" if dry_run else "will run"
    auth = f"with HTTP basic auth for {args.composer_auth_host}" if args.composer_auth_host else "without auth"
    print(f"{prefix} in {args.working_directory} on PHP {args.php_version}:")
    print(f"  {INSTALL_COMMAND} ({auth})")
    for name, _, command in CHECKS:
        if name in checks:
            print(f"  {command}")


def write_output(name, value):
    path = os.environ.get("GITHUB_OUTPUT", "")
    if path == "":
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--php-version", required=True)
    parser.add_argument("--php-extensions", default="")
    parser.add_argument("--has-cs-fixer", required=True)
    parser.add_argument("--has-phpstan", required=True)
    parser.add_argument("--has-rector", required=True)
    parser.add_argument("--working-directory", required=True)
    parser.add_argument("--composer-auth-host", default="")
    parser.add_argument("--composer-auth-username", default="")
    parser.add_argument("--has-composer-auth-password", required=True)
    parser.add_argument("--dry-run", required=True)
    return parser.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    workspace = os.environ.get("GITHUB_WORKSPACE") or os.getcwd()
    try:
        checks = plan(args, workspace)
    except InputError as error:
        print(f"::error::{error}")
        return 1
    report(checks, args, args.dry_run == "true")
    write_output("checks", " ".join(checks))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
