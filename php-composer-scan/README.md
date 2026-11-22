# php-composer-scan

Audits the Composer dependencies of a project for known vulnerabilities and fails only on a blocking severity. Publishes nothing.

A composite action, `php-composer-scan/action.yml`: it audits in a job of the caller, after the caller (or a component such as [`php-composer-setup`](../php-composer-setup/README.md)) set up Composer and installed the dependencies.

The `*-check` components of this repository run the composite action after their install, with their input `audit-blocking-severities`.

## What it does

1. Validates the inputs before anything else: `working-directory` stays inside the checkout and holds a `composer.json`; `blocking-severities` lists one or more of `critical`, `high`, `medium`, `low`.
2. Prints the audit command. A dry run stops here.
3. Runs `composer audit --format=json --abandoned=report --no-interaction` on the installed packages (`vendor/composer/installed.json`), or with `--locked` on `composer.lock` when nothing is installed. Neither: the audit fails and asks for `composer install` first. When that list holds no package, there is nothing to audit (Composer prints no report then) and the step passes with zero advisories.
4. Prints every advisory as an annotation: an error when its severity is in `blocking-severities`, a warning otherwise. An advisory without a severity is a warning. Abandoned packages are warnings.
5. Fails when at least one advisory has a blocking severity, or when Composer prints no JSON report (for example, the advisory database is unreachable).

Composer applies the `config.audit.ignore` of `composer.json` before the report is read: that list is where a project records an advisory it does not remediate, with the reason.

## What it deletes

Nothing. The component writes no file.

## Inputs

| Input | Default | Meaning |
| --- | --- | --- |
| `working-directory` | `.` | Directory that holds `composer.json`, relative to the checkout. |
| `blocking-severities` | `critical` | Severities that fail the audit, separated by commas. |
| `dry-run` | `false` | Validate and print the command only. |

## Outputs

| Output | Meaning |
| --- | --- |
| `command` | The audit command, run or planned (without `--locked`, which is decided at run time). |
| `blocking-severities` | The blocking severities, comma-separated, from the highest. |
| `advisories` | Number of advisories found (empty in a dry run). |
| `blocking` | Number of advisories with a blocking severity (empty in a dry run). |

## Variables and secrets

The action reads no variable and no secret. A caller that keeps the severities in a variable passes them in `with:`, for example `blocking-severities: ${{ vars.PHP_COMPOSER_SCAN_BLOCKING_SEVERITIES || 'critical' }}`. Credentials for private dependencies go to the install step (`php-composer-setup`), not to the audit.

## Permissions

The caller grants `contents: read` to the calling job. The component asks for nothing more.

## Example

Reference the component by its major tag `php-composer-scan-v1`. A fork and a caller outside the maintainer organization pin the full commit SHA of a release tag instead, with the tag as a comment (`@<sha40> # php-composer-scan-vX.Y.Z`). Find the SHA with `gh api repos/<owner>/actions/git/ref/tags/php-composer-scan-vX.Y.Z` (dereference an annotated tag).

The composite action after an install in a job of the caller:

```yaml
steps:
  - uses: <owner>/actions/php-composer-setup@php-composer-setup-v1
    with:
      php-version: "8.4"
  - uses: <owner>/actions/php-composer-scan@php-composer-scan-v1
    with:
      blocking-severities: critical, high
```
