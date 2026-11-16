# php-library-check

Validates a PHP library (a Composer package) in a change: a dependency audit, the static checks and the tests, one job per PHP version. Publishes nothing.

Two forms, one name:

- reusable workflow `.github/workflows/php-library-check.yml`: checks out the calling repository and runs one job per PHP version of `php-versions`. The static checks run in the job of the first version; the audit and the tests run in every job;
- composite action `php-library-check/action.yml`: checks one PHP version in a job of the caller, on a checkout the caller made.

## What it does

1. Validates the inputs before anything else: `path` stays inside the checkout and holds a `composer.json`; each script input is a Composer script name; booleans are `true` or `false`.
2. Plans the Composer commands and prints them. A dry run stops here, after the dry runs of the two actions below.
3. Sets up PHP and Composer and runs `composer install` in `path` through [`php-composer-setup`](../php-composer-setup/README.md). Without a `composer.lock`, Composer resolves the dependencies for the PHP version of the job, or for the `config.platform.php` of `composer.json` when it sets one. With `unset-platform-php: true`, that entry is removed first; with `dependency-versions: highest`, `composer update` runs in place of `composer install` (see "Highest dependency versions").
4. Audits the installed dependencies through [`php-composer-scan`](../php-composer-scan/README.md): an advisory whose severity is in `audit-blocking-severities` fails the job; lower severities and abandoned packages are warnings. There is no input to turn the audit off; an advisory that a project does not remediate goes to `config.audit.ignore` of its `composer.json`, with the reason.
5. With `expect-majors`, reads the installed versions (`composer show --format=json`) and fails the job when a named package is missing or at another major version, before any check runs.
6. Runs the planned commands in `path`, in order; the first failure fails the job:
   - static checks (when `static-checks` is true): `composer validate --no-check-publish --no-check-lock` (plus `--strict` with `composer-validate-strict`), `composer dump-autoload --optimize --strict-psr`, then the Composer scripts `lint-script`, `phpstan-script`, `cs-fixer-script`, `rector-script` and `composer-scripts`, each skipped when empty;
   - tests: the Composer script `test-script`, skipped when empty.

The tools themselves (PHPStan, PHP-CS-Fixer, Rector, PHPUnit) come from the library's own `require-dev` and configuration: the component calls the library's Composer scripts. The commands run without a shell, as argument lists, so an input cannot add a command.

## What it deletes

Nothing. The component publishes nothing and commits nothing. `composer install` writes `vendor/` in the checkout of the job, which ends with the job. With `unset-platform-php: true` it rewrites `composer.json` there without `config.platform.php`, and `composer update` (`dependency-versions: highest`) writes `composer.lock` there; both changes end with the job. With `composer-auth`, `php-composer-setup` writes the global Composer `auth.json` of the runner, outside the checkout.

## Inputs

| Input | Workflow | Action | Default | Meaning |
| --- | --- | --- | --- | --- |
| `php-versions` | yes | no | `["8.2", "8.3", "8.4"]` | JSON array of PHP versions, one job each. The static checks run on the first one: list the lowest supported version first. |
| `php-version` | no | yes (required) | — | PHP version of this job. |
| `static-checks` | no | yes | `true` | Run the static checks in this job. The workflow sets it for the first PHP version only. |
| `path` | yes | yes | `.` | Directory of the library (the one that holds `composer.json`). |
| `php-extensions` | yes | yes | `mbstring, xml, curl, intl` | PHP extensions, separated by commas. |
| `composer-validate` | yes (boolean) | yes | `true` | Run `composer validate --no-check-publish --no-check-lock`. |
| `composer-validate-strict` | yes (boolean) | yes | `false` | Add `--strict`: warnings fail too. |
| `strict-psr` | yes (boolean) | yes | `true` | Run `composer dump-autoload --optimize --strict-psr`: a class outside its PSR-4 path fails. |
| `lint-script` | yes | yes | empty | Composer script of the PHP syntax lint. |
| `phpstan-script` | yes | yes | `check:stan` | Composer script that runs PHPStan. |
| `cs-fixer-script` | yes | yes | `check:style` | Composer script that runs PHP-CS-Fixer without fixing. |
| `rector-script` | yes | yes | `check:rector` | Composer script that runs Rector in dry-run mode. |
| `composer-scripts` | yes | yes | empty | More Composer scripts, static checks too, separated by commas or new lines. |
| `test-script` | yes | yes | `test` | Composer script that runs the tests, in every job. |
| `dependency-versions` | yes | yes | `locked` | `locked` runs `composer install` (from `composer.lock` when there is one); `highest` runs `composer update`. |
| `unset-platform-php` | yes (boolean) | yes | `false` | Remove `config.platform.php` from `composer.json` before the install, so that the PHP version of the job constrains the resolution. |
| `expect-majors` | yes | yes | empty | Installed packages and their major version, `<vendor>/<package>:<major>`, separated by commas or new lines. A package that is missing or at another major fails the job. |
| `audit-blocking-severities` | yes | yes | variable `PHP_LIBRARY_CHECK_AUDIT_BLOCKING_SEVERITIES`, else `critical` (workflow); `critical` (action) | Severities that fail the dependency audit, separated by commas: any of `critical`, `high`, `medium`, `low`. |
| `composer-auth` | secret | input | empty | Composer credentials in the `COMPOSER_AUTH` JSON format, for private dependencies. |
| `dry-run` | yes (boolean) | yes (`true`/`false`) | `false` | Validate and plan only. |
| `timeout-minutes` | yes | no | `20` | Timeout of each job. |

An empty script input skips its step; a script named by an input but missing from `composer.json` fails the job.

## Outputs

| Output | Meaning |
| --- | --- |
| `commands` | JSON array of the Composer commands planned after the install, in order. In the workflow, the value of the last job that ended: pass one PHP version for a stable value. |
| `audit-command` | The dependency audit command, run or planned. |
| `install-command` | The `composer install` or `composer update` command, run or planned. |
| `platform-command` | The command that removes `config.platform.php`, run or planned; empty unless `unset-platform-php` is `true`. |
| `expect-majors` | JSON object of the expected major version of each package of `expect-majors`; `{}` when empty. |

## Variables and secrets

| Name | Kind | Read by |
| --- | --- | --- |
| `PHP_LIBRARY_CHECK_AUDIT_BLOCKING_SEVERITIES` | variable | default of `audit-blocking-severities` in the reusable workflow |
| `PHP_LIBRARY_CHECK_COMPOSER_AUTH` | secret | passed by the caller to `composer-auth`, one explicit line |

## Permissions

The caller grants `contents: read` to the calling job. The component asks for nothing more.

## Example

Reference the component by its major tag `php-library-check-v1`. A fork and a caller outside the maintainer organization pin the full commit SHA of a release tag instead, with the tag as a comment (`@<sha40> # php-library-check-vX.Y.Z`). Find the SHA with `gh api repos/<owner>/actions/git/ref/tags/php-library-check-vX.Y.Z` (dereference an annotated tag).

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

permissions: {}

jobs:
  php-library-check:
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/php-library-check.yml@php-library-check-v1
    with:
      php-versions: '["8.2", "8.3", "8.4"]'
      composer-validate-strict: true
      composer-scripts: check:schemas
    secrets:
      composer-auth: ${{ secrets.PHP_LIBRARY_CHECK_COMPOSER_AUTH }}
```

### Highest dependency versions

A library that pins `config.platform.php` to its lowest supported PHP version resolves the same dependencies on every PHP version: a `^7.4 || ^8.1` constraint stays on 7.4 even in a PHP 8.4 job. A second call with `dependency-versions: highest` and `unset-platform-php: true` resolves for the PHP version of each job instead, and `expect-majors` fails the job when the resolution did not reach the major version under test, so the job cannot pass on the older major.

`unset-platform-php` lets the PHP version of the job constrain the resolution. Do not pass `--ignore-platform-req=php` for this: it admits packages that the PHP version of the job cannot run (a package that requires PHP 8.4 installs on PHP 8.3, and `composer check-platform-reqs` then fails).

List only the PHP versions on which the expected major version can install: a job on a PHP version that the new major does not support resolves the older major and fails `expect-majors`, as it should.

```yaml
jobs:
  php-library-check:
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/php-library-check.yml@php-library-check-v1
    with:
      php-versions: '["8.3", "8.4", "8.5"]'

  php-library-check-highest:
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/php-library-check.yml@php-library-check-v1
    with:
      php-versions: '["8.4", "8.5"]'
      dependency-versions: highest
      unset-platform-php: true
      expect-majors: |
        symfony/console:8
        symfony/http-kernel:8
      # The static checks already run in the locked job.
      composer-validate: false
      strict-psr: false
      phpstan-script: ""
      cs-fixer-script: ""
      rector-script: ""
```

The composite action in a job of the caller:

```yaml
steps:
  - uses: actions/checkout@<sha40> # vX.Y.Z
    with:
      persist-credentials: false
  - uses: <owner>/actions/php-library-check@php-library-check-v1
    with:
      php-version: "8.4"
      static-checks: "false"
```

A pull request from a fork gets no secrets: a library with private dependencies cannot be installed there.
