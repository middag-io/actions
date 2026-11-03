# php-composer-setup

Sets up PHP and Composer in the current job, writes the Composer credentials and installs the dependencies of the calling repository. It is the shared first stage of PHP jobs that check, test or package a project: those jobs call the composite action as a step and run their own commands after it.

Two forms, one name:

- composite action: `php-composer-setup/action.yml`, used as a step in your job;
- reusable workflow: `.github/workflows/php-composer-setup.yml`, a job of its own that proves the dependencies install. Its result does not reach your other jobs.

## What it does

1. Validates every input before any write: PHP version, extensions, coverage driver, booleans, the working directory (relative, inside the checkout, with a `composer.json`), and the HTTP Basic host and user name.
2. Sets up PHP with [`shivammathur/setup-php`](https://github.com/shivammathur/setup-php), with Composer.
3. Restores the Composer download cache (`composer config --global cache-files-dir`), keyed by the runner OS, the PHP version and the hash of `composer.lock` in the working directory, when `cache` and `install` are `true`. A run without an exact hit restores the latest cache of the same OS and PHP version, and the job saves a new one at its end.
4. Writes the `composer-auth` entries, the HTTP Basic credentials of one Composer repository and a GitHub token, when given, to the global Composer `auth.json` (`$COMPOSER_HOME/auth.json`, mode `0600`), outside the checkout. Each string of `composer-auth` other than a `username` is masked in the log before anything is written.
5. Runs `composer install --no-interaction --prefer-dist` in the working directory, with `--no-dev` and `--optimize-autoloader` when asked.

In a dry run it only validates the inputs and reports the plan: no PHP setup, no credentials written, no install.

## What it deletes or overwrites

- Deletes no file of its own. `composer install` removes from `vendor/` every package that the lock file does not list; with `no-dev: true` that includes the development packages a previous install left there.
- Overwrites, in the global Composer `auth.json` of the runner, each host entry that `composer-auth` names, then the `http-basic` entry of `http-basic-host` and the `github-oauth` entry of `github.com`: for the same entry, `http-basic-host` and `github-token` win over `composer-auth`. Other entries stay.
- With `cache: true`, the cache step of [`actions/cache`](https://github.com/actions/cache) replaces the Composer download cache of the runner with the restored one, and saves a new cache entry in the calling repository at the end of the job when the key had no exact hit. A repository without `composer.lock` gets one fixed key per OS and PHP version, saved once; set `cache: false` there, or commit the lock file. Without `github-token`, the `github-oauth` entry that `shivammathur/setup-php` writes (the job token) stays.
- `shivammathur/setup-php` changes the runner for the rest of the job: it switches the default `php` and `composer`, installs the extensions and writes their `php.ini` settings.
- `composer install` creates or replaces `vendor/` in the working directory, and writes `composer.lock` there when the repository has none. It runs the install scripts of the calling repository (`post-install-cmd` and the others), which can change more files.

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
| `composer-auth`       | no                                        | empty                                                                 | JSON object in the `COMPOSER_AUTH` format (`http-basic`, `github-oauth`, `bearer` and the other `auth.json` keys); composite action only, the workflow takes the secret `composer-auth` |
| `cache`               | no                                        | `true`                                                                | restore and save the Composer download cache, keyed by `composer.lock`; used only when `install` is `true`                          |
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
| `auth-entries`    | credentials configured, comma-separated: `composer-auth`, `http-basic:<host>`, `github-oauth:github.com`; the planned ones in a dry run |
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
| `PHP_COMPOSER_SETUP_COMPOSER_AUTH`       | secret   | `composer-auth`                                 |

The caller passes each secret with one explicit line. Never `secrets: inherit`. A caller that keeps the user name in a variable and the password in a secret builds `composer-auth` in the calling workflow:

```yaml
composer-auth: ${{ format('{{"http-basic":{{"<host>":{{"username":"{0}","password":"{1}"}}}}}}', vars.<USERNAME_VARIABLE>, secrets.<PASSWORD_SECRET>) }}
```

The password comes from a secret, so the runner masks it in every log line; the action masks it again inside the JSON value. A token minted in the calling job by a GitHub App (for private Composer packages hosted as GitHub repositories) goes to the `github-token` input of the composite action directly.

## Permissions

`contents: read`, for the checkout. The action itself needs no token permission. The cache step uses the runner's cache service, which needs no permission in `permissions:`.

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
