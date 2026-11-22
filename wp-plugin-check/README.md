# wp-plugin-check

Checks a WordPress plugin in a change. It publishes nothing.

- **PHP** (`target: php`), one PHP version per call: sets up PHP with the chosen extensions and runs `composer install --no-interaction --prefer-dist` through the [`php-composer-setup`](../php-composer-setup/README.md) action of the same release, with its Composer download cache keyed by `composer.lock`, then each Composer script of `composer-scripts` (for example `check:style`, `check:stan`, `check:rector`, `test`). A failed install stops the job. A failed script does not: every script runs, and the job fails at the end with the list of failed scripts.
- **UI** (`target: ui`): sets up Node.js, runs `npm ci`, `tsc --noEmit` (unless `typecheck` is false), then each npm script of `npm-scripts`. Without `github-packages-npm-scope`, it installs from the URLs of `package-lock.json` without a token. For a scope on GitHub Packages, see "UI with GitHub Packages".

- **Dependency audit**, in each job: after the install of the PHP job, `composer audit` through the [`php-composer-scan`](../php-composer-scan/README.md) action of the same release; before `npm ci` of the UI job, `npm audit` on `package-lock.json` through [`node-npm-scan`](../node-npm-scan/README.md). An advisory whose severity is in `audit-blocking-severities` fails the job; lower severities (and abandoned Composer packages) are warnings. No input turns the audit off.

Before any setup, the component validates its inputs and the plugin files: each Composer script must be declared in `composer.json`, each npm script in `package.json`, and `package-lock.json` must exist.

A composite action, `wp-plugin-check/action.yml`: one target (`php` or `ui`) in a job of your own, after your own checkout. Several PHP versions are a job matrix of the caller; the UI check is one more job (see "Example").

## What it deletes or overwrites

Nothing outside the runner. On the runner, `composer install` and `npm ci` write `vendor/` and `node_modules/` in the checkout, as they do locally, and the Composer and npm scripts do what the plugin declares. With Composer credentials, `php-composer-setup` writes them to the global Composer `auth.json` of the runner (mode `0600`, outside the checkout); no `auth.json` is written to the checkout. The Composer cache step saves a download cache entry in the calling repository at the end of the job when its key had no exact hit.

## Credentials

The Composer credentials go to the global Composer `auth.json` of the runner, outside the checkout, before `composer install`, and stay there until the end of the job: Composer plugins, the Composer scripts of `composer.json` and the commands they run can read them, as they can read any file of the runner. The npm token reaches only the registry access check and `npm ci`; the TypeScript check and the npm scripts run without it.

Code that the npm install runs gets the same environment: the lifecycle scripts of the npm packages (`preinstall`, `install`, `postinstall`) see `NODE_AUTH_TOKEN`. Install only dependencies you trust with these credentials.

| Credential                                                        | Reaches                                                                       | Used for                                                                                                                                                                                                                |
| ----------------------------------------------------------------- | ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| secret `composer-auth`                                            | the global Composer `auth.json`, written by `php-composer-setup`              | private Composer repositories (`http-basic`, `bearer` and the other keys of the [COMPOSER_AUTH](https://getcomposer.org/doc/03-cli.md#composer-auth) format)                                                            |
| `app-client-id` and secret `app-private-key`                      | the global Composer `auth.json`, as `github-oauth` for `github.com`           | private Composer packages hosted on GitHub. The token reads, with `contents: read` only, the repositories of `app-repositories` (same owner as the calling repository), or only the calling repository when it is empty |
| job token, composite action with `github-packages-npm-scope` only | the registry access check and `npm ci`, as `NODE_AUTH_TOKEN`                  | npm packages of that scope on GitHub Packages. Without the scope, `npm ci` follows the URLs of `package-lock.json` with no token at all                                                                                 |

## Inputs

| Input                       | Default         | Description                                                                                                                                         |
| --------------------------- | --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `target`                    | required        | What to check: `php` or `ui`.                                                                                                                       |
| `path`                      | `.`             | Plugin directory (`composer.json`), relative to the checkout.                                                                                       |
| `php-version`               | empty           | PHP version of the `php` target (for example `8.4`); required for `php`.                                                                            |
| `php-extensions`            | `mbstring, xml` | PHP extensions to install, separated by commas.                                                                                                     |
| `composer-scripts`          | empty           | Composer scripts to run after `composer install`, separated by commas or new lines. Empty runs only the install.                                    |
| `composer-auth`             | empty           | Composer credentials, a JSON object in the `COMPOSER_AUTH` format, passed from a secret.                                                            |
| `app-client-id`             | empty           | Client id of a GitHub App for private Composer packages on GitHub. Empty uses no App.                                                               |
| `app-private-key`           | empty           | Private key (PEM) of the GitHub App of `app-client-id`, passed from a secret; required with `app-client-id`, unless `dry-run`.                      |
| `app-repositories`          | empty           | Repositories of the calling repository's owner that the App token reads, separated by commas or new lines. Empty reads the calling repository only. |
| `ui-directory`              | `ui`            | UI directory (`package.json`, `package-lock.json`) of the `ui` target, relative to `path`.                                                          |
| `node-version`              | `22`            | Node.js version of the `ui` target.                                                                                                                 |
| `npm-scripts`               | empty           | npm scripts to run after `npm ci`, separated by commas or new lines.                                                                                |
| `typecheck`                 | `true`          | Run `tsc --noEmit` in the UI directory, with the TypeScript installed by `npm ci`.                                                                  |
| `github-packages-npm-scope` | empty           | npm scope installed from GitHub Packages with the job token (see "UI with GitHub Packages").                                                        |
| `audit-blocking-severities` | `critical`      | Severities of a dependency advisory that fail the job, separated by commas: any of `critical`, `high`, `medium`, `low`.                             |
| `dry-run`                   | `false`         | Validate the inputs and the plugin files and report the commands only: no PHP or Node.js setup, no install, no credentials.                         |

## Secrets and variables

The action reads no variable and no secret by itself. The caller passes each one in `with:`, one explicit line each:

| Name                                        | Kind     | Passed to                   |
| ------------------------------------------- | -------- | --------------------------- |
| `WP_PLUGIN_CHECK_COMPOSER_AUTH`             | secret   | `composer-auth`             |
| `WP_PLUGIN_CHECK_APP_PRIVATE_KEY`           | secret   | `app-private-key`           |
| `WP_PLUGIN_CHECK_APP_CLIENT_ID`             | variable | `app-client-id`             |
| `WP_PLUGIN_CHECK_APP_REPOSITORIES`          | variable | `app-repositories`          |
| `WP_PLUGIN_CHECK_AUDIT_BLOCKING_SEVERITIES` | variable | `audit-blocking-severities` |

Example of `WP_PLUGIN_CHECK_COMPOSER_AUTH` for a private Composer repository with HTTP Basic authentication:

```json
{
  "http-basic": {
    "composer.example.com": { "username": "reader", "password": "..." }
  }
}
```

## Outputs

| Output        | Description                               |
| ------------- | ----------------------------------------- |
| `commands`    | Number of commands of the plan of the target. |
| `plan-sha256` | SHA-256 of the plan of the target.        |

The plan has one line per command: the directory relative to the workspace, a tab, then the command and its arguments, each line ending with a new line. The plan does not depend on the PHP version.

## Permissions

Grant the calling job `contents: read`. The component asks for nothing more; the `ui` target with a GitHub Packages scope also needs `packages: read`.

## UI with GitHub Packages

A UI whose `package-lock.json` resolves a scope from GitHub Packages (`https://npm.pkg.github.com`) needs the job token with `packages: read`. Grant it to the UI job only:

```yaml
check-ui:
  runs-on: ubuntu-24.04
  timeout-minutes: 20
  permissions:
    contents: read
    # Read-only: npm ci reads the scope from GitHub Packages with the job token.
    packages: read
  steps:
    - uses: actions/checkout@<sha40> # vX.Y.Z
      with:
        persist-credentials: false
    - uses: <owner>/actions/wp-plugin-check@wp-plugin-check-v1
      with:
        target: ui
        ui-directory: ui
        npm-scripts: lint, build
        github-packages-npm-scope: "@owner"
```

With `github-packages-npm-scope`, the action first checks read access to every package of that scope that `package-lock.json` resolves from GitHub Packages, and reports every package without access in one run; then it gives the job token to `npm ci` only. Each package must grant the calling repository Read access (package settings, "Manage Actions access"). A GitHub App installation token is refused by that registry; the job token is the identity that works there.

## Example

```yaml
jobs:
  check-php:
    runs-on: ubuntu-24.04
    timeout-minutes: 20
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
      - uses: <owner>/actions/wp-plugin-check@wp-plugin-check-v1
        with:
          target: php
          php-version: ${{ matrix.php }}
          php-extensions: mbstring, xml, intl
          composer-scripts: check:style, check:stan, check:rector, test
          composer-auth: ${{ secrets.WP_PLUGIN_CHECK_COMPOSER_AUTH }}
          app-client-id: ${{ vars.WP_PLUGIN_CHECK_APP_CLIENT_ID }}
          app-private-key: ${{ secrets.WP_PLUGIN_CHECK_APP_PRIVATE_KEY }}

  check-ui:
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@<sha40> # vX.Y.Z
        with:
          persist-credentials: false
      - uses: <owner>/actions/wp-plugin-check@wp-plugin-check-v1
        with:
          target: ui
          npm-scripts: lint, build
```

Reference the component by its major tag `wp-plugin-check-v1`. A fork and a caller outside the maintainer organization pin the full commit SHA of a release tag instead, with the tag as a comment (`@<sha40> # wp-plugin-check-vX.Y.Z`). Find the SHA of a release tag with `gh api repos/<owner>/actions/git/ref/tags/wp-plugin-check-vX.Y.Z`; when the object type is `tag`, dereference it with `gh api repos/<owner>/actions/git/tags/<object-sha>`.
