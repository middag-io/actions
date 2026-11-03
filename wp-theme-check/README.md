# wp-theme-check

Checks a WordPress theme in a change. The component installs the theme's Composer dependencies and runs the theme's own PHP tools in read-only mode, once per PHP version:

- PHP CS Fixer: `vendor/bin/php-cs-fixer fix --dry-run --diff`
- PHPStan: `vendor/bin/phpstan analyse`
- Rector: `vendor/bin/rector process --dry-run`

The tools and their configuration files come from the theme (`require-dev` in `composer.json`, `.php-cs-fixer.dist.php`, `phpstan.neon`, `rector.php`). The component publishes nothing.

PHP, Composer, the credentials and `composer install --no-interaction --prefer-dist` come from the [`php-composer-setup`](../php-composer-setup/README.md) action of the same release, with its download cache keyed by `composer.lock`.

It has two forms:

- the reusable workflow `.github/workflows/wp-theme-check.yml`: one job per PHP version of `php-versions`, the checkout of the calling repository, the variable fallback and the secret;
- the composite action `wp-theme-check/action.yml`: one PHP version, for a caller that already has its own job and checkout.

## What it deletes

Nothing. It writes only inside the runner: `vendor/` in the theme directory, the Composer cache, and, when `composer-auth-host` is set, the `http-basic` entry of that host in the global Composer `auth.json` of the runner (mode `0600`, outside the checkout). No `auth.json` is written to the checkout. The cache step saves a Composer download cache entry in the calling repository at the end of the job when its key had no exact hit.

## Inputs

Reusable workflow (`workflow_call`):

| Input                    | Type    | Default                                          | Description                                                                                                    |
| ------------------------ | ------- | ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------- |
| `php-versions`           | string  | `["8.3", "8.4"]`                                 | JSON array of the PHP versions to check with, one job each.                                                    |
| `php-extensions`         | string  | `mbstring, xml`                                  | PHP extensions to install, separated by commas (setup-php syntax).                                             |
| `has-cs-fixer`           | boolean | `true`                                           | Run PHP CS Fixer in dry-run mode.                                                                              |
| `has-phpstan`            | boolean | `true`                                           | Run PHPStan.                                                                                                   |
| `has-rector`             | boolean | `false`                                          | Run Rector in dry-run mode.                                                                                    |
| `working-directory`      | string  | `.`                                              | Theme directory that holds `composer.json`, relative to the checkout.                                          |
| `composer-auth-host`     | string  | variable `WP_THEME_CHECK_COMPOSER_AUTH_HOST`     | Host of a private Composer repository that needs HTTP basic auth. Empty for none.                              |
| `composer-auth-username` | string  | variable `WP_THEME_CHECK_COMPOSER_AUTH_USERNAME` | HTTP basic username for `composer-auth-host`.                                                                  |
| `dry-run`                | boolean | `false`                                          | Validate the inputs and the theme directory and print the plan only: no PHP setup, no install, no credentials. |
| `timeout-minutes`        | number  | `15`                                             | Timeout of each job, in minutes.                                                                               |

The composite action takes the same inputs, with these differences: `php-version` (one version, required) in place of `php-versions`; `composer-auth-password` as an input; no `timeout-minutes`; booleans as the strings `true` or `false`; no variable fallback (README of the repository, "Variables and secrets").

At least one of `has-cs-fixer`, `has-phpstan` and `has-rector` must be `true`. The first step validates every input before any other step and fails with an error that names the input and its variable: a PHP version that is not `X.Y`, a `working-directory` outside the checkout or without a valid `composer.json`, a username without a host, a host without a username, or, outside a dry run, a host without a password.

## Outputs

| Output   | Description                                                                             |
| -------- | --------------------------------------------------------------------------------------- |
| `checks` | Enabled checks, separated by spaces, in run order: `php-cs-fixer`, `phpstan`, `rector`. |

## Variables and secrets

| Name                                    | Kind     | Read by                                          | Required                                  |
| --------------------------------------- | -------- | ------------------------------------------------ | ----------------------------------------- |
| `WP_THEME_CHECK_COMPOSER_AUTH_HOST`     | variable | default of `composer-auth-host`                  | only for a private Composer repository    |
| `WP_THEME_CHECK_COMPOSER_AUTH_USERNAME` | variable | default of `composer-auth-username`              | when the host is set                      |
| `WP_THEME_CHECK_COMPOSER_AUTH_PASSWORD` | secret   | passed by the caller as `composer-auth-password` | when the host is set, except in a dry run |

The reusable workflow declares the secret `composer-auth-password`. The caller passes it with one explicit line; never `secrets: inherit`. In a dry run the workflow does not pass the secret on.

## Permissions

The caller grants `contents: read` to the job that calls the reusable workflow. The component asks for nothing more.

## Example

Reusable workflow, pinned by the full commit SHA of a release tag of this component:

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

permissions: {}

jobs:
  check:
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/wp-theme-check.yml@<sha40> # wp-theme-check-v1.0.0
    with:
      php-versions: '["8.4"]'
      has-rector: true
    secrets:
      composer-auth-password: ${{ secrets.WP_THEME_CHECK_COMPOSER_AUTH_PASSWORD }}
```

Composite action, in a job of the caller:

```yaml
steps:
  - uses: actions/checkout@<sha40> # vX.Y.Z
    with:
      persist-credentials: false
  - uses: <owner>/actions/wp-theme-check@<sha40> # wp-theme-check-v1.0.0
    with:
      php-version: "8.4"
      has-rector: "true"
      composer-auth-host: ${{ vars.WP_THEME_CHECK_COMPOSER_AUTH_HOST }}
      composer-auth-username: ${{ vars.WP_THEME_CHECK_COMPOSER_AUTH_USERNAME }}
      composer-auth-password: ${{ secrets.WP_THEME_CHECK_COMPOSER_AUTH_PASSWORD }}
```

Find the SHA of a tag with `gh api repos/<owner>/actions/git/ref/tags/wp-theme-check-vX.Y.Z`; dereference an annotated tag with `gh api repos/<owner>/actions/git/tags/<object-sha>`.

## Tests

```bash
python3 -I -B -m unittest discover -s wp-theme-check/tests -v
```

The fixture theme in `tests/fixtures/basic/` feeds the unit tests and the CI dry run, which calls the reusable workflow with `dry-run: true` and expects the output `checks` to be `php-cs-fixer phpstan`.
