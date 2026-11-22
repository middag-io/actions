# wp-theme-check

Checks a WordPress theme in a change. The component installs the theme's Composer dependencies and runs the theme's own PHP tools in read-only mode, with one PHP version per call:

- PHP CS Fixer: `vendor/bin/php-cs-fixer fix --dry-run --diff`
- PHPStan: `vendor/bin/phpstan analyse`
- Rector: `vendor/bin/rector process --dry-run`

The tools and their configuration files come from the theme (`require-dev` in `composer.json`, `.php-cs-fixer.dist.php`, `phpstan.neon`, `rector.php`). The component publishes nothing.

Before the tools, the component audits the installed dependencies through the [`php-composer-scan`](../php-composer-scan/README.md) action of the same release: an advisory whose severity is in `audit-blocking-severities` fails the job; lower severities and abandoned packages are warnings. No input turns the audit off; an advisory the theme does not remediate goes to `config.audit.ignore` of its `composer.json`, with the reason.

PHP, Composer, the credentials and `composer install --no-interaction --prefer-dist` come from the [`php-composer-setup`](../php-composer-setup/README.md) action of the same release, with its download cache keyed by `composer.lock`.

It is a composite action, `wp-theme-check/action.yml`: one PHP version, in a job of the caller, after its checkout. Several PHP versions are a job matrix of the caller.

## What it deletes

Nothing. It writes only inside the runner: `vendor/` in the theme directory, the Composer cache, and, when `composer-auth-host` is set, the `http-basic` entry of that host in the global Composer `auth.json` of the runner (mode `0600`, outside the checkout). No `auth.json` is written to the checkout. The cache step saves a Composer download cache entry in the calling repository at the end of the job when its key had no exact hit.

## Inputs

| Input                       | Default         | Description                                                                                                             |
| --------------------------- | --------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `php-version`               | required        | PHP version to check with (for example `8.4`).                                                                          |
| `php-extensions`            | `mbstring, xml` | PHP extensions to install, separated by commas (setup-php syntax).                                                      |
| `has-cs-fixer`              | `true`          | Run PHP CS Fixer in dry-run mode (`true` or `false`).                                                                   |
| `has-phpstan`               | `true`          | Run PHPStan (`true` or `false`).                                                                                        |
| `has-rector`                | `false`         | Run Rector in dry-run mode (`true` or `false`).                                                                         |
| `working-directory`         | `.`             | Theme directory that holds `composer.json`, relative to the checkout.                                                   |
| `composer-auth-host`        | empty           | Host of a private Composer repository that needs HTTP basic auth. Empty for none.                                       |
| `composer-auth-username`    | empty           | HTTP basic username for `composer-auth-host`.                                                                           |
| `composer-auth-password`    | empty           | HTTP basic password for `composer-auth-host`, passed from a secret.                                                     |
| `audit-blocking-severities` | `critical`      | Severities of a dependency advisory that fail the job, separated by commas: any of `critical`, `high`, `medium`, `low`. |
| `dry-run`                   | `false`         | Validate the inputs and the theme directory and print the plan only: no PHP setup, no install, no credentials.          |

At least one of `has-cs-fixer`, `has-phpstan` and `has-rector` must be `true`. The first step validates every input before any other step and fails with an error that names the input and its variable: a PHP version that is not `X.Y`, a `working-directory` outside the checkout or without a valid `composer.json`, a username without a host, a host without a username, or, outside a dry run, a host without a password.

## Outputs

| Output   | Description                                                                             |
| -------- | --------------------------------------------------------------------------------------- |
| `checks` | Enabled checks, separated by spaces, in run order: `php-cs-fixer`, `phpstan`, `rector`. |

## Variables and secrets

The action reads no variable and no secret by itself. The caller passes each one in `with:`, one explicit line each:

| Name                                       | Kind     | Passed to                   | Required                                  |
| ------------------------------------------ | -------- | --------------------------- | ----------------------------------------- |
| `WP_THEME_CHECK_COMPOSER_AUTH_HOST`        | variable | `composer-auth-host`        | only for a private Composer repository    |
| `WP_THEME_CHECK_COMPOSER_AUTH_USERNAME`    | variable | `composer-auth-username`    | when the host is set                      |
| `WP_THEME_CHECK_AUDIT_BLOCKING_SEVERITIES` | variable | `audit-blocking-severities` | no (default `critical`)                   |
| `WP_THEME_CHECK_COMPOSER_AUTH_PASSWORD`    | secret   | `composer-auth-password`    | when the host is set, except in a dry run |

## Permissions

The caller grants `contents: read` to the calling job. The component asks for nothing more.

## Example

```yaml
jobs:
  check:
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    permissions:
      contents: read
    strategy:
      fail-fast: false
      matrix:
        php: ["8.3", "8.4"]
    steps:
      - uses: actions/checkout@<sha40> # vX.Y.Z
        with:
          persist-credentials: false
      - uses: <owner>/actions/wp-theme-check@wp-theme-check-v1
        with:
          php-version: ${{ matrix.php }}
          has-rector: "true"
          composer-auth-host: ${{ vars.WP_THEME_CHECK_COMPOSER_AUTH_HOST }}
          composer-auth-username: ${{ vars.WP_THEME_CHECK_COMPOSER_AUTH_USERNAME }}
          composer-auth-password: ${{ secrets.WP_THEME_CHECK_COMPOSER_AUTH_PASSWORD }}
```

Reference the component by its major tag `wp-theme-check-v1`. A fork and a caller outside the maintainer organization pin the full commit SHA of a release tag instead, with the tag as a comment (`@<sha40> # wp-theme-check-vX.Y.Z`). Find the SHA of a tag with `gh api repos/<owner>/actions/git/ref/tags/wp-theme-check-vX.Y.Z`; dereference an annotated tag with `gh api repos/<owner>/actions/git/tags/<object-sha>`.

## Tests

```bash
python3 -I -B -m unittest discover -s wp-theme-check/tests -v
```

The fixture theme in `tests/fixtures/basic/` feeds the unit tests.
