"""Plans and runs the checks of a WordPress plugin (wp-plugin-check).

Two targets:

- php: composer install, then each Composer script of --composer-scripts. The
  install runs before this script, in php-composer-setup; the plan lists it
  and "run" skips it.
- ui: npm ci in the UI directory, then an optional TypeScript check and each
  npm script of --npm-scripts.

"plan" validates the inputs and the plugin files, prints the commands and
writes the outputs "commands" and "plan-sha256" to $GITHUB_OUTPUT. "run" does
the same, then runs the commands. The npm token reaches only npm ci and the
registry access check before it, as NODE_AUTH_TOKEN. The Composer credentials
never reach this script: php-composer-setup writes them to the global
Composer auth.json of the runner.

Standard library only. Run as python3 -I.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

GITHUB_PACKAGES_REGISTRY = "https://npm.pkg.github.com"
SCRIPT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]*$")
PHP_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+$")
COMPOSER_INSTALL_ARGV = ("composer", "install", "--no-interaction", "--prefer-dist")
NPM_SCOPE_RE = re.compile(r"^@[a-z0-9][a-z0-9._-]*$")
# Two forms of "resolved" for GitHub Packages in package-lock.json:
# https://npm.pkg.github.com/@scope/name/-/name-1.0.0.tgz and
# https://npm.pkg.github.com/download/@scope/name/1.0.0/<digest>.
RESOLVED_RE = re.compile(r"^https://npm\.pkg\.github\.com/(?:download/)?(@[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*)/")


class InputError(Exception):
    """An input or a plugin file is not valid; the message names it."""


def split_list(value):
    """Splits a list given with commas or new lines; drops empty items."""
    return [item.strip() for item in re.split(r"[,\n]", value or "") if item.strip()]


def parse_bool(name, value):
    if value in ("true", "false"):
        return value == "true"
    raise InputError(f"input {name} must be true or false, got {value!r}")


def relative_dir(name, value, base):
    """Resolves a directory given relative to base; refuses to leave base."""
    if os.path.isabs(value) or ".." in value.replace("\\", "/").split("/"):
        raise InputError(f"input {name} must be a relative path without '..', got {value!r}")
    path = os.path.normpath(os.path.join(base, value))
    if not os.path.isdir(path):
        raise InputError(f"input {name}: directory {value!r} not found")
    return path


def read_json(path, what):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise InputError(f"{what} not found: {path}") from None
    except (OSError, ValueError) as error:
        raise InputError(f"{what} is not valid JSON: {path}: {error}") from None
    if not isinstance(data, dict):
        raise InputError(f"{what} is not a JSON object: {path}")
    return data


def script_names(name, value, declared, where):
    """Validates script names and checks that each one is declared."""
    names = split_list(value)
    for item in names:
        if not SCRIPT_NAME_RE.match(item):
            raise InputError(f"input {name}: invalid script name {item!r}")
        if item not in declared:
            raise InputError(f"input {name}: script {item!r} is not declared in {where}")
    return names


def github_packages(lock, scope):
    """Names of the packages of scope that the lockfile resolves from GitHub Packages."""
    names = set()
    for entry in (lock.get("packages") or {}).values():
        match = RESOLVED_RE.match(str((entry or {}).get("resolved", "")))
        if match and match.group(1).startswith(scope + "/"):
            names.add(match.group(1))
    return sorted(names)


def plan_php(args, root):
    if not PHP_VERSION_RE.match(args.php_version):
        raise InputError(f"input php-version must look like 8.3, got {args.php_version!r}")
    if args.has_app_client_id != args.has_app_private_key:
        raise InputError("inputs app-client-id and app-private-key go together: set both or neither")
    composer = read_json(os.path.join(root, "composer.json"), "composer.json")
    declared = composer.get("scripts") or {}
    scripts = script_names("composer-scripts", args.composer_scripts, declared, "composer.json")
    # php-composer-setup runs this install, with its defaults, before "run".
    commands = [{"argv": list(COMPOSER_INSTALL_ARGV), "cwd": root, "setup": True}]
    commands += [{"argv": ["composer", "run-script", "--no-interaction", name], "cwd": root} for name in scripts]
    return commands


def plan_ui(args, root):
    ui = relative_dir("ui-directory", args.ui_directory, root)
    package = read_json(os.path.join(ui, "package.json"), "package.json")
    lock = read_json(os.path.join(ui, "package-lock.json"), "package-lock.json")
    scripts = script_names("npm-scripts", args.npm_scripts, package.get("scripts") or {}, "package.json")
    commands = []
    if args.github_packages_npm_scope:
        if not NPM_SCOPE_RE.match(args.github_packages_npm_scope):
            raise InputError(f"input github-packages-npm-scope must look like @owner, got {args.github_packages_npm_scope!r}")
        for name in github_packages(lock, args.github_packages_npm_scope):
            argv = ["npm", "view", name, "version", f"--registry={GITHUB_PACKAGES_REGISTRY}"]
            commands.append({"argv": argv, "cwd": ui, "credential": "npm", "probe": True})
    commands.append({"argv": ["npm", "ci"], "cwd": ui, "credential": "npm"})
    if args.typecheck:
        commands.append({"argv": ["npx", "--no-install", "tsc", "--noEmit"], "cwd": ui})
    commands += [{"argv": ["npm", "run", name], "cwd": ui} for name in scripts]
    return commands


def build_plan(args, workspace):
    root = relative_dir("path", args.path, workspace)
    return plan_php(args, root) if args.target == "php" else plan_ui(args, root)


def plan_text(commands, workspace):
    """One line per command: the directory relative to the workspace, a tab, the arguments."""
    lines = []
    for command in commands:
        cwd = os.path.relpath(command["cwd"], workspace).replace(os.sep, "/")
        lines.append(cwd + "\t" + " ".join(command["argv"]) + "\n")
    return "".join(lines)


def write_outputs(commands, text):
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"commands={len(commands)}\nplan-sha256={digest}\n")
    print(f"commands={len(commands)} plan-sha256={digest}")


def command_env(command, base_env):
    """The environment of one command: the npm token only for the npm install commands."""
    env = {k: v for k, v in base_env.items() if k != "WP_PLUGIN_CHECK_NPM_TOKEN"}
    if command.get("credential") == "npm" and base_env.get("WP_PLUGIN_CHECK_NPM_TOKEN"):
        env["NODE_AUTH_TOKEN"] = base_env["WP_PLUGIN_CHECK_NPM_TOKEN"]
    return env


def run_one(command, base_env, runner):
    line = " ".join(command["argv"])
    print(f"::group::{line}", flush=True)
    result = runner(command["argv"], cwd=command["cwd"], env=command_env(command, base_env), check=False)
    print("::endgroup::", flush=True)
    return line, result.returncode


def run_commands(commands, base_env, runner=subprocess.run):
    """Runs the commands; returns the failed ones.

    Every registry probe runs, so one run reports every package without
    access; any failed probe stops before the install. A failed install stops
    at once. Every check runs, so one run reports every failed check. The
    commands that php-composer-setup already ran are skipped.
    """
    probes = [c for c in commands if c.get("probe")]
    steps = [c for c in commands if not c.get("probe") and not c.get("setup")]
    failed = []
    for command in probes:
        line, code = run_one(command, base_env, runner)
        if code != 0:
            failed.append(line)
            print(f"::error::no read access to {command['argv'][2]} on GitHub Packages: grant this repository Read access to the package and packages: read to the calling job")
    if failed:
        return failed
    for command in steps:
        line, code = run_one(command, base_env, runner)
        if code == 0:
            continue
        failed.append(line)
        print(f"::error::{line} failed (exit {code})")
        if command.get("credential"):
            return failed
    return failed


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=["plan", "run"])
    parser.add_argument("--target", choices=["php", "ui"], required=True)
    parser.add_argument("--path", default=".")
    parser.add_argument("--php-version", default="")
    parser.add_argument("--composer-scripts", default="")
    parser.add_argument("--ui-directory", default="ui")
    parser.add_argument("--npm-scripts", default="")
    parser.add_argument("--typecheck", default="true")
    parser.add_argument("--github-packages-npm-scope", default="")
    parser.add_argument("--has-app-client-id", default="false")
    parser.add_argument("--has-app-private-key", default="false")
    args = parser.parse_args(argv)
    args.typecheck = parse_bool("typecheck", args.typecheck)
    args.has_app_client_id = parse_bool("has-app-client-id", args.has_app_client_id)
    args.has_app_private_key = parse_bool("has-app-private-key", args.has_app_private_key)
    return args


def main(argv=None):
    workspace = os.environ.get("GITHUB_WORKSPACE") or os.getcwd()
    try:
        args = parse_args(argv)
        commands = build_plan(args, workspace)
    except InputError as error:
        print(f"::error::{error}")
        return 1
    text = plan_text(commands, workspace)
    print(text, end="")
    write_outputs(commands, text)
    if args.mode == "plan":
        return 0
    failed = run_commands(commands, dict(os.environ))
    if failed:
        print("::error::failed: " + "; ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
