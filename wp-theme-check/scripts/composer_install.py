"""Runs `composer install` in the theme directory.

When COMPOSER_AUTH_HOST is set, the HTTP basic credentials reach Composer only
through the COMPOSER_AUTH environment variable of the child process: nothing is
written to auth.json or to any other file of the checkout, and the password is
never printed.

Environment: WORKING_DIRECTORY (required), COMPOSER_AUTH_HOST,
COMPOSER_AUTH_USERNAME, COMPOSER_AUTH_PASSWORD. check_inputs.py has validated
them before this script runs.

Standard library only. Run as: python3 -I composer_install.py
"""

import json
import os
import subprocess
import sys

INSTALL_ARGS = ("composer", "install", "--no-interaction", "--prefer-dist", "--no-progress")


def composer_env(environ):
    """Returns a new environment for Composer; never mutates environ."""
    child = {key: value for key, value in environ.items() if not key.startswith("COMPOSER_AUTH_")}
    host = environ.get("COMPOSER_AUTH_HOST", "")
    if host == "":
        return child
    auth = {
        "http-basic": {
            host: {
                "username": environ.get("COMPOSER_AUTH_USERNAME", ""),
                "password": environ.get("COMPOSER_AUTH_PASSWORD", ""),
            }
        }
    }
    return {**child, "COMPOSER_AUTH": json.dumps(auth, separators=(",", ":"))}


def main(environ):
    directory = environ.get("WORKING_DIRECTORY", "")
    if directory == "":
        print("::error::WORKING_DIRECTORY is empty")
        return 1
    try:
        completed = subprocess.run(INSTALL_ARGS, cwd=directory, env=composer_env(environ), check=False)
    except FileNotFoundError:
        print("::error::composer not found on PATH")
        return 1
    if completed.returncode != 0:
        print(f"::error::composer install failed with exit code {completed.returncode}")
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main(dict(os.environ)))
