# php-composer-setup

Sets up PHP and Composer in the current job, writes the Composer credentials and installs the dependencies of the calling repository. It is the shared first stage of PHP jobs that check, test or package a project: those jobs call the composite action as a step and run their own commands after it.

Two forms, one name:

- composite action: `php-composer-setup/action.yml`, used as a step in your job;
- reusable workflow: `.github/workflows/php-composer-setup.yml`, a job of its own that proves the dependencies install. Its result does not reach your other jobs.

## What it does

1. Validates every input before any write: PHP version, extensions, coverage driver, booleans, the working directory (relative, inside the checkout, with a `composer.json`), and the HTTP Basic host and user name.
2. Sets up PHP with [`shivammathur/setup-php`](https://github.com/shivammathur/setup-php), with Composer.
3. Writes the HTTP Basic credentials of one Composer repository and a GitHub token, when given, to the global Composer `auth.json` (`$COMPOSER_HOME/auth.json`, mode `0600`), outside the checkout.
4. Runs `composer install --no-interaction --prefer-dist` in the working directory, with `--no-dev` and `--optimize-autoloader` when asked.

In a dry run it only validates the inputs and reports the plan: no PHP setup, no credentials written, no install.

## What it deletes or overwrites

- Deletes nothing.
- Overwrites, in the global Composer `auth.json` of the runner, the `http-basic` entry of `http-basic-host` and the `github-oauth` entry of `github.com`. Other entries stay.
- `composer install` creates or replaces `vendor/` in the working directory, and writes `composer.lock` there when the repository has none.

The credentials stay in the runner's Composer home until the job ends. They are never written inside the checkout, so a later step that archives the checkout does not pick them up.

## Inputs

| Input                 | Required                                  | Default                                                               | Description                                                                                                                         |
| --------------------- | ----------------------------------------- | --------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `php-version`         | yes (workflow: no)                        | workflow: `PHP_COMPOSER_SETUP_PHP_VERSION`, then `8.4`                | `8.4`, `latest`, `nightly` or `pre-installed`                                                                                       |
| `php-extensions`      | no                                        | `mbstring, xml` (workflow: `PHP_COMPOSER_SETUP_PHP_EXTENSIONS` first) | comma-separated, in the `shivammathur/setup-php` syntax                                                                             |
| `coverage`            | no                                        | `none`                                                                | `none`, `xdebug`, `xdebug2` or `pcov`                                                                                               |
| `working-directory`   | no                                        | `.`                                                                   | directory that holds `composer.json`, relative to the checkout                                                                      |
| `http-basic-host`     | no                                        | empty (workflow: `PHP_COMPOSER_SETUP_HTTP_BASIC_HOST`)                | host of a Composer repository that needs HTTP Basic authentication, with an optional port                                           |
| `http-basic-username` | with `http-basic-host`                    | empty (workflow: `PHP_COMPOSER_SETUP_HTTP_BASIC_USERNAME`)            | HTTP Basic user name                                                                                                                |
| `http-basic-password` | with `http-basic-host`, outside a dry run | empty                                                                 | composite action only; the workflow takes the secret `http-basic-password`                                                          |
| `github-token`        | no                                        | empty                                                                 | composite action only; the workflow takes the secret `github-token`. Empty keeps the token that `shivammathur/setup-php` configures |
| `install`             | no                                        | `true`                                                                | run `composer install`                                                                                                              |
| `no-dev`              | no                                        | `false`                                                               | pass `--no-dev`                                                                                                                     |
| `optimize-autoloader` | no                                        | `false`                                                               | pass `--optimize-autoloader`                                                                                                        |
| `dry-run`             | no                                        | `false`                                                               | validate and report only                                                                                                            |
| `timeout-minutes`     | no                                        | `15`                                                                  | workflow only: timeout of the job                                                                                                   |

The composite action takes `true` or `false` strings for booleans; the workflow takes `type: boolean` inputs.

## Outputs

| Output            | Description                                                                                                            |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `php-version`     | PHP version installed, in SemVer format; empty in a dry run                                                            |
| `auth-entries`    | credentials configured, comma-separated: `http-basic:<host>`, `github-oauth:github.com`; the planned ones in a dry run |
| `install-command` | the `composer install` command, run or planned; empty when `install` is `false`                                        |

## Variables and secrets

The reusable workflow fills its inputs from these variables of the caller (repository first, then organization) when the caller passes no value:

| Name                                     | Kind     | Input                                           |
| ---------------------------------------- | -------- | ----------------------------------------------- |
| `PHP_COMPOSER_SETUP_PHP_VERSION`         | variable | `php-version`                                   |
| `PHP_COMPOSER_SETUP_PHP_EXTENSIONS`      | variable | `php-extensions`                                |
| `PHP_COMPOSER_SETUP_HTTP_BASIC_HOST`     | variable | `http-basic-host`                               |
| `PHP_COMPOSER_SETUP_HTTP_BASIC_USERNAME` | variable | `http-basic-username`                           |
| `PHP_COMPOSER_SETUP_HTTP_BASIC_PASSWORD` | secret   | `http-basic-password`                           |
| `PHP_COMPOSER_SETUP_GITHUB_TOKEN`        | secret   | `github-token`, when a long-lived token is used |

The caller passes each secret with one explicit line. Never `secrets: inherit`. A token minted in the calling job by a GitHub App (for private Composer packages hosted as GitHub repositories) goes to the `github-token` input of the composite action directly.

## Permissions

`contents: read`, for the checkout. The action itself needs no token permission.

## Examples

Composite action, in a job of your own that then runs its checks:

```yaml
jobs:
  check:
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    permissions:
      contents: read
    strategy:
      matrix:
        php-version: ["8.3", "8.4"]
    steps:
      - uses: actions/checkout@<sha40> # vX.Y.Z
        with:
          persist-credentials: false
      - uses: <owner>/actions/php-composer-setup@<sha40> # php-composer-setup-vX.Y.Z
        with:
          php-version: ${{ matrix.php-version }}
          http-basic-host: ${{ vars.PHP_COMPOSER_SETUP_HTTP_BASIC_HOST }}
          http-basic-username: ${{ vars.PHP_COMPOSER_SETUP_HTTP_BASIC_USERNAME }}
          http-basic-password: ${{ secrets.PHP_COMPOSER_SETUP_HTTP_BASIC_PASSWORD }}
      - run: composer check:stan
```

Reusable workflow:

```yaml
jobs:
  setup:
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/php-composer-setup.yml@<sha40> # php-composer-setup-vX.Y.Z
    with:
      php-version: "8.4"
    secrets:
      http-basic-password: ${{ secrets.PHP_COMPOSER_SETUP_HTTP_BASIC_PASSWORD }}
```

Pin the full 40-character commit SHA of a `php-composer-setup-vX.Y.Z` release tag, with the tag as a comment. Never `@main`.
