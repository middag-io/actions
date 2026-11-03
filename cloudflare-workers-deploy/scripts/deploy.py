"""Plans and runs a Cloudflare Workers deployment with Wrangler.

Two subcommands, called by action.yml:

  plan    validates every input and the project directory, writes a plan file
          and the step outputs, and prints what the deployment would do. It
          reads no credential and touches no network.
  deploy  reads the plan file and runs `wrangler deploy` from the project's
          own dependencies (`npm exec --no`), with the API token that the
          step passes in CLOUDFLARE_API_TOKEN. It prints the Wrangler output
          and writes the version id as a step output.

Standard library only. Run as `python3 -I`.
"""

import argparse
import json
import os
import re
import subprocess
import sys

CONFIG_CANDIDATES = ("wrangler.jsonc", "wrangler.json", "wrangler.toml")
CONFIG_EXTENSIONS = (".jsonc", ".json", ".toml")
WRANGLER_PACKAGE = "wrangler"
DEPENDENCY_FIELDS = ("dependencies", "devDependencies")
LOCKFILE = "package-lock.json"
PLAN_FILE_NAME = "plan.json"

ENVIRONMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
WORKER_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
SCRIPT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,99}$")
ACCOUNT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
NODE_VERSION_RE = re.compile(r"^(?:[0-9]{1,3}(?:\.[0-9]{1,4}){0,2}|lts/(?:\*|[a-z]{1,20}))$")
VERSION_ID_RE = re.compile(
    r"Current Version ID:\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)


class InputError(Exception):
    """An input or the project directory fails a rule. The message names it."""


def parse_bool(name, value):
    if value in ("true", "false"):
        return value == "true"
    raise InputError(f"input {name} must be true or false, got {value!r}")


def relative_path(name, value, base):
    """Resolves a relative path under base; refuses absolute paths, '..' and links out of base."""
    if not value or "\x00" in value:
        raise InputError(f"input {name} is empty or holds a NUL character")
    if os.path.isabs(value) or value.startswith("~"):
        raise InputError(f"input {name} must be a relative path, got {value!r}")
    if ".." in value.replace("\\", "/").split("/"):
        raise InputError(f"input {name} must not contain '..', got {value!r}")
    real_base = os.path.realpath(base)
    resolved = os.path.realpath(os.path.join(real_base, value))
    if resolved != real_base and not resolved.startswith(real_base + os.sep):
        raise InputError(f"input {name} resolves outside {base!r}: {value!r}")
    return resolved


def check_pattern(name, value, pattern, rule):
    if value and not pattern.match(value):
        raise InputError(f"input {name} must match {rule}, got {value!r}")
    return value


def read_package(project_dir):
    path = os.path.join(project_dir, "package.json")
    if not os.path.isfile(path):
        raise InputError("working-directory has no package.json")
    try:
        with open(path, encoding="utf-8") as handle:
            package = json.load(handle)
    except (OSError, ValueError) as error:
        raise InputError(f"package.json of working-directory is not valid JSON: {error}") from error
    if not isinstance(package, dict):
        raise InputError("package.json of working-directory is not a JSON object")
    return package


def check_package(project_dir, build_script):
    """Requires the lockfile, Wrangler as a dependency and the build script."""
    package = read_package(project_dir)
    if not os.path.isfile(os.path.join(project_dir, LOCKFILE)):
        raise InputError(f"working-directory has no {LOCKFILE}; the component installs with npm ci")
    declared = any(
        isinstance(package.get(field), dict) and WRANGLER_PACKAGE in package[field]
        for field in DEPENDENCY_FIELDS
    )
    if not declared:
        raise InputError(
            "package.json does not declare wrangler in dependencies or devDependencies; "
            "the component runs the project's own Wrangler"
        )
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    if build_script and build_script not in scripts:
        raise InputError(f"input build-script names {build_script!r}, which is not a script of package.json")


def find_config(project_dir, value):
    """Returns the Wrangler configuration path, relative to project_dir."""
    if value:
        path = relative_path("wrangler-config", value, project_dir)
        if not path.endswith(CONFIG_EXTENSIONS):
            raise InputError(f"input wrangler-config must end in {', '.join(CONFIG_EXTENSIONS)}, got {value!r}")
        if not os.path.isfile(path):
            raise InputError(f"input wrangler-config names a missing file: {value!r}")
        return os.path.relpath(path, os.path.realpath(project_dir))
    for candidate in CONFIG_CANDIDATES:
        if os.path.isfile(os.path.join(project_dir, candidate)):
            return candidate
    raise InputError(
        f"working-directory has none of {', '.join(CONFIG_CANDIDATES)}; set the input wrangler-config"
    )


def wrangler_args(plan):
    """Arguments after `npm exec --no --`."""
    args = [WRANGLER_PACKAGE, "deploy", "--config", plan["wrangler-config"]]
    if plan["wrangler-environment"]:
        args += ["--env", plan["wrangler-environment"]]
    if plan["worker-name"]:
        args += ["--name", plan["worker-name"]]
    return args


def build_plan(args, workspace):
    dry_run = parse_bool("dry-run", args.dry_run)
    has_api_token = parse_bool("has-api-token", args.has_api_token)
    if not dry_run and not has_api_token:
        raise InputError("input api-token is empty; pass it from a secret or set dry-run: true")
    project_dir = relative_path("working-directory", args.working_directory, workspace)
    if not os.path.isdir(project_dir):
        raise InputError(f"input working-directory is not a directory: {args.working_directory!r}")
    plan = {
        "working-directory": os.path.relpath(project_dir, os.path.realpath(workspace)),
        "node-version": check_pattern("node-version", args.node_version, NODE_VERSION_RE, "a Node.js version or lts/<name>"),
        "build-script": check_pattern("build-script", args.build_script, SCRIPT_NAME_RE, SCRIPT_NAME_RE.pattern),
        "wrangler-environment": check_pattern(
            "wrangler-environment", args.wrangler_environment, ENVIRONMENT_RE, ENVIRONMENT_RE.pattern
        ),
        "worker-name": check_pattern("worker-name", args.worker_name, WORKER_NAME_RE, WORKER_NAME_RE.pattern),
        "account-id": check_pattern("account-id", args.account_id, ACCOUNT_ID_RE, "32 lowercase hexadecimal characters"),
        "dry-run": dry_run,
    }
    if not plan["node-version"]:
        raise InputError("input node-version is empty")
    check_package(project_dir, plan["build-script"])
    plan["wrangler-config"] = find_config(project_dir, args.wrangler_config)
    return plan


def write_outputs(values):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def print_plan(plan):
    mode = "dry run: nothing is installed, built or deployed" if plan["dry-run"] else "deployment"
    print(f"cloudflare-workers-deploy plan ({mode})")
    print(f"  working-directory: {plan['working-directory']}")
    print(f"  node-version: {plan['node-version']}")
    print("  install: npm ci")
    print(f"  build: {'npm run ' + plan['build-script'] if plan['build-script'] else '(none)'}")
    print(f"  deploy: npm exec --no -- {' '.join(wrangler_args(plan))}")
    print(f"  account-id: {'from input' if plan['account-id'] else 'from the Wrangler configuration'}")


def command_plan(args):
    plan = build_plan(args, os.environ.get("GITHUB_WORKSPACE") or os.getcwd())
    os.makedirs(args.out_dir, exist_ok=True)
    plan_file = os.path.join(args.out_dir, PLAN_FILE_NAME)
    with open(plan_file, "w", encoding="utf-8") as handle:
        json.dump(plan, handle, indent=2, sort_keys=True)
    print_plan(plan)
    write_outputs(
        {
            "plan-file": plan_file,
            "working-directory": plan["working-directory"],
            "node-version": plan["node-version"],
            "build-script": plan["build-script"],
            "wrangler-config": plan["wrangler-config"],
        }
    )


def read_plan(path):
    try:
        with open(path, encoding="utf-8") as handle:
            plan = json.load(handle)
    except (OSError, ValueError) as error:
        raise InputError(f"cannot read the plan file {path!r}: {error}") from error
    if plan.get("dry-run") is not False:
        raise InputError("the plan is a dry run; refusing to deploy")
    return plan


def deploy_env(plan):
    env = dict(os.environ)
    if not env.get("CLOUDFLARE_API_TOKEN"):
        raise InputError("CLOUDFLARE_API_TOKEN is empty; pass the input api-token")
    if plan["account-id"]:
        env["CLOUDFLARE_ACCOUNT_ID"] = plan["account-id"]
    return env


def run_wrangler(plan, env, runner=subprocess.Popen):
    """Streams the Wrangler output and returns (exit code, version id)."""
    command = ["npm", "exec", "--no", "--", *wrangler_args(plan)]
    print(f"running: {' '.join(command)}", flush=True)
    version_id = ""
    process = runner(
        command,
        cwd=plan["working-directory"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    for line in process.stdout:
        sys.stdout.write(line)
        match = VERSION_ID_RE.search(line)
        if match:
            version_id = match.group(1)
    return process.wait(), version_id


def command_deploy(args, runner=subprocess.Popen):
    plan = read_plan(args.plan_file)
    code, version_id = run_wrangler(plan, deploy_env(plan), runner)
    if code != 0:
        raise InputError(f"wrangler deploy exited with status {code}")
    if not version_id:
        print("::warning::wrangler deploy succeeded, but its output named no version id")
    write_outputs({"version-id": version_id})


def parser():
    root = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = root.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    for name in (
        "working-directory",
        "node-version",
        "build-script",
        "wrangler-config",
        "wrangler-environment",
        "worker-name",
        "account-id",
        "dry-run",
        "has-api-token",
        "out-dir",
    ):
        plan.add_argument(f"--{name}", required=True)
    deploy = commands.add_parser("deploy")
    deploy.add_argument("--plan-file", required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "plan":
            command_plan(args)
        else:
            command_deploy(args)
    except InputError as error:
        print(f"::error::{error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
