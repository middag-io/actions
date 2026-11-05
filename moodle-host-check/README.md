# moodle-host-check

Checks that code (a Composer package: a library or a plugin) loads and runs inside a real Moodle installation, on each Moodle version of a support matrix. Publishes nothing.

Two forms, one name:

- reusable workflow `.github/workflows/moodle-host-check.yml`: checks out the calling repository and runs one job per Moodle host (a Moodle branch and a PHP version), each with its own empty PostgreSQL service;
- composite action `moodle-host-check/action.yml`: checks one host in a job of the caller, on a checkout the caller made, with a PostgreSQL database the caller provides.

## What it does

1. Validates the inputs before anything else: `moodle-branch` is `MOODLE_<NNN>_STABLE` or `main`, `moodle-repository` is `owner/name`, `path` stays inside the checkout and holds a `composer.json`, `probe-script` (when set) is a `.php` file of the checkout, and the database inputs are plain names and a TCP port.
2. Plans the commands and prints them. A dry run stops here.
3. Sets up PHP and Composer through the [`php-composer-setup`](../php-composer-setup/README.md) action of the same release, and runs `composer install` in `path`. Without a `composer.lock`, Composer resolves the dependencies for the PHP version of the host, so each host gets the versions its PHP line accepts.
4. Clones `moodle-branch` of `moodle-repository` (depth 1) into the temporary directory of the runner, outside the checkout. When the `composer.json` of Moodle requires Composer packages (Moodle 5.2 and later), runs `composer install --no-dev` there.
5. Writes `config.php` for PostgreSQL (`db-host`, `db-port`, `db-name`, `db-user`, no password) and runs `admin/cli/install_database.php` with a random administrator password, which the log never shows (it prints `<generated>` in its place).
6. Runs the probe with `php -d max_input_vars=5000`, from `path`. The probe gets two environment variables:
   - `MOODLE_HOST_CHECK_CONFIG`: the `config.php` of the host; the probe defines `CLI_SCRIPT` and requires it;
   - `MOODLE_HOST_CHECK_CODE_DIR`: the absolute path of `path`, whose `vendor/autoload.php` loads the code under test.

   A non-zero exit status of the probe fails the job.

The built-in probe (`probe-script` empty) boots the host and the Composer autoloader of the code under test, prints the Moodle release, branch and version, the PHP version and the database driver, and reads the site version back from the database. A probe of your own exercises your code: for example, it calls the parts of your library that adapt to the Moodle version, and fails when a call fails.

The commands run without a shell, as argument lists, so an input cannot add a command.

## What it deletes

Nothing in the repository. The host lives in the temporary directory of the runner and in the database of the job, which end with the job. In the composite action form, the install creates the Moodle tables in `db-name`: give it an empty database of its own.

## Inputs

| Input | Workflow | Action | Default | Meaning |
| --- | --- | --- | --- | --- |
| `hosts` | yes (required) | no | — | JSON array of hosts, one job each, as objects with `moodle-branch` and `php-version`. |
| `moodle-branch` | no | yes (required) | — | Moodle branch of this host, for example `MOODLE_405_STABLE` or `main`. |
| `php-version` | no | yes (required) | — | PHP version of this host. It must be one that the Moodle branch accepts. |
| `moodle-repository` | yes | yes | `moodle/moodle` | GitHub repository to clone Moodle from. |
| `php-extensions` | yes | yes | `pgsql, intl, zip, gd, soap, xml, mbstring, curl, sodium, exif` | PHP extensions, separated by commas. |
| `path` | yes | yes | `.` | Directory of the code under test (the one that holds `composer.json`), relative to the checkout. |
| `probe-script` | yes | yes | empty | PHP script of the checkout to run against the host. Empty: the built-in probe. |
| `db-host` | no | yes | `127.0.0.1` | Host of the PostgreSQL database. |
| `db-port` | no | yes | `5432` | Port of the PostgreSQL database. |
| `db-name` | no | yes | `moodle` | Empty database that receives the Moodle tables. |
| `db-user` | no | yes | `postgres` | User that logs in without a password (trust authentication) and owns `db-name`. |
| `composer-auth` | secret | input | empty | Composer credentials in the `COMPOSER_AUTH` JSON format, for private dependencies of the code under test. |
| `dry-run` | yes (boolean) | yes (`true`/`false`) | `false` | Validate and plan only: no database, no PHP set-up, no clone, no install, no probe. |
| `timeout-minutes` | yes | no | `30` | Timeout of each job. |

The reusable workflow runs PostgreSQL 17 as a service of each job, pinned by digest, with trust authentication; the service is reachable only from its job.

## Outputs

| Output | Meaning |
| --- | --- |
| `commands` | JSON array of the planned commands, with `<moodle>` for the Moodle directory, `<generated>` for the administrator password and `<builtin-probe>` for the built-in probe. In the workflow, the value of the last host job that ended: pass one host for a stable value. |

## Variables and secrets

No input reads a variable. The caller passes the secret with one explicit line:

| Name | Kind | Read by |
| --- | --- | --- |
| `MOODLE_HOST_CHECK_COMPOSER_AUTH` | secret | passed by the caller to `composer-auth`, one explicit line |

## Permissions

The caller grants `contents: read` to the calling job. The component asks for nothing more. The Moodle clone is anonymous.

## Example

Reference the component by the full commit SHA of a release tag of this component, with the tag as a comment. Find the SHA with `gh api repos/<owner>/actions/git/ref/tags/moodle-host-check-vX.Y.Z` (dereference an annotated tag).

```yaml
name: Moodle support matrix

on:
  pull_request:
  push:
    branches: [main]

permissions: {}

jobs:
  moodle-host-check:
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/moodle-host-check.yml@<sha40> # moodle-host-check-vX.Y.Z
    with:
      hosts: >-
        [
          {"moodle-branch": "MOODLE_405_STABLE", "php-version": "8.3"},
          {"moodle-branch": "MOODLE_500_STABLE", "php-version": "8.4"},
          {"moodle-branch": "MOODLE_501_STABLE", "php-version": "8.4"}
        ]
      probe-script: tests/host-probe.php
    secrets:
      composer-auth: ${{ secrets.MOODLE_HOST_CHECK_COMPOSER_AUTH }}
```

A probe of your own:

```php
<?php
define('CLI_SCRIPT', true);
require(getenv('MOODLE_HOST_CHECK_CONFIG'));
require_once(getenv('MOODLE_HOST_CHECK_CODE_DIR') . '/vendor/autoload.php');

// Call the code under test here; throw or exit non-zero on a failure.
echo \Example\Library\Version::moodleBranch(), "\n";
```

The composite action in a job of the caller, with a database service of the caller:

```yaml
jobs:
  host:
    runs-on: ubuntu-24.04
    permissions:
      contents: read
    services:
      postgres:
        image: postgres:17@sha256:<digest>
        env:
          POSTGRES_DB: moodle
          POSTGRES_HOST_AUTH_METHOD: trust
        ports:
          - 5432:5432
        options: --health-cmd pg_isready --health-interval 5s --health-retries 12
    steps:
      - uses: actions/checkout@<sha40> # vX.Y.Z
        with:
          persist-credentials: false
      - uses: <owner>/actions/moodle-host-check@<sha40> # moodle-host-check-vX.Y.Z
        with:
          moodle-branch: MOODLE_405_STABLE
          php-version: "8.3"
```

A pull request from a fork gets no secrets: a code under test with private dependencies cannot be installed there.
