# php-composer-scan

Audits the Composer dependencies of a project for known vulnerabilities and fails only on a blocking severity. Publishes nothing.

Two forms, one name:

- reusable workflow `.github/workflows/php-composer-scan.yml`: checks out the calling repository, sets up PHP and installs the dependencies with [`php-composer-setup`](../php-composer-setup/README.md), then audits them;
- composite action `php-composer-scan/action.yml`: audits in a job of the caller, after the caller (or a component such as `php-composer-setup`) set up Composer and installed the dependencies.

The `*-check` components of this repository run the composite action after their install, with their input `audit-blocking-severities`.

## What it does

1. Validates the inputs before anything else: `working-directory` stays inside the checkout and holds a `composer.json`; `blocking-severities` lists one or more of `critical`, `high`, `medium`, `low`.
2. Prints the audit command. A dry run stops here.
3. Runs `composer audit --format=json --abandoned=report --no-interaction` on the installed packages (`vendor/composer/installed.json`), or with `--locked` on `composer.lock` when nothing is installed. Neither: the audit fails and asks for `composer install` first.
4. Prints every advisory as an annotation: an error when its severity is in `blocking-severities`, a warning otherwise. An advisory without a severity is a warning. Abandoned packages are warnings.
5. Fails when at least one advisory has a blocking severity, or when Composer prints no JSON report (for example, the advisory database is unreachable).

Composer applies the `config.audit.ignore` of `composer.json` before the report is read: that list is where a project records an advisory it does not remediate, with the reason.

## What it deletes

Nothing. The component writes no file.

## Inputs

| Input | Workflow | Action | Default | Meaning |
| --- | --- | --- | --- | --- |
| `working-directory` | yes | yes | `.` | Directory that holds `composer.json`, relative to the checkout. |
| `blocking-severities` | yes | yes | variable `PHP_COMPOSER_SCAN_BLOCKING_SEVERITIES`, else `critical` (workflow); `critical` (action) | Severities that fail the audit, separated by commas. |
| `php-version` | yes | no | `8.4` | PHP version that resolves the dependencies. |
| `php-extensions` | yes | no | `mbstring, xml` | PHP extensions, in the `shivammathur/setup-php` syntax. |
| `composer-auth` | secret | no | empty | Composer credentials in the `COMPOSER_AUTH` JSON format, for private dependencies. |
| `dry-run` | yes (boolean) | yes (`true`/`false`) | `false` | Validate and print the command only. |
| `timeout-minutes` | yes | no | `15` | Timeout of the job. |

## Outputs

| Output | Meaning |
| --- | --- |
| `command` | The audit command, run or planned (without `--locked`, which is decided at run time). |
| `blocking-severities` | The blocking severities, comma-separated, from the highest. |
| `advisories` | Number of advisories found (empty in a dry run). |
| `blocking` | Number of advisories with a blocking severity (empty in a dry run). |

## Variables and secrets

| Name | Kind | Read by |
| --- | --- | --- |
| `PHP_COMPOSER_SCAN_BLOCKING_SEVERITIES` | variable | default of `blocking-severities` in the reusable workflow |
| `PHP_COMPOSER_SCAN_COMPOSER_AUTH` | secret | passed by the caller to `composer-auth`, one explicit line |

## Permissions

The caller grants `contents: read` to the calling job. The component asks for nothing more.

## Example

Reference the component by the full commit SHA of a release tag of this component, with the tag as a comment. Find the SHA with `gh api repos/<owner>/actions/git/ref/tags/php-composer-scan-vX.Y.Z` (dereference an annotated tag).

```yaml
jobs:
  php-composer-scan:
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/php-composer-scan.yml@<sha40> # php-composer-scan-vX.Y.Z
    with:
      blocking-severities: critical, high
    secrets:
      composer-auth: ${{ secrets.PHP_COMPOSER_SCAN_COMPOSER_AUTH }}
```

The composite action after an install in a job of the caller:

```yaml
steps:
  - uses: <owner>/actions/php-composer-setup@<sha40> # php-composer-setup-vX.Y.Z
    with:
      php-version: "8.4"
  - uses: <owner>/actions/php-composer-scan@<sha40> # php-composer-scan-vX.Y.Z
    with:
      blocking-severities: critical, high
```
