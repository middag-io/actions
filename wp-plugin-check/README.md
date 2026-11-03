# wp-plugin-check

Checks a WordPress plugin in a change. It publishes nothing.

- **PHP**, one job per PHP version: sets up PHP with the chosen extensions, runs `composer install`, then each Composer script of `composer-scripts` (for example `check:style`, `check:stan`, `check:rector`, `test`). A failed install stops the job. A failed script does not: every script runs, and the job fails at the end with the list of failed scripts.
- **UI**, one job when `ui-directory` is set: sets up Node.js, runs `npm ci`, `tsc --noEmit` (unless `typecheck` is false), then each npm script of `npm-scripts`. The reusable workflow installs from the URLs of `package-lock.json` without a token. For a scope on GitHub Packages, see "UI with GitHub Packages".

Before any setup, the component validates its inputs and the plugin files: each Composer script must be declared in `composer.json`, each npm script in `package.json`, and `package-lock.json` must exist.

Two forms, one name:

- reusable workflow `.github/workflows/wp-plugin-check.yml`: checks out the calling repository and runs the jobs above;
- composite action `wp-plugin-check/action.yml`: one target (`php` or `ui`) in a job of your own, after your own checkout.

## What it deletes or overwrites

Nothing outside the runner. On the runner, `composer install` and `npm ci` write `vendor/` and `node_modules/` in the checkout, as they do locally, and the Composer and npm scripts do what the plugin declares.

## Credentials

Each credential reaches only the command that installs dependencies. The other commands, the Composer scripts and the npm scripts among them, run without it.

| Credential                                                        | Reaches                                                                       | Used for                                                                                                                                                                                                                |
| ----------------------------------------------------------------- | ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| secret `composer-auth`                                            | `composer install`, as `COMPOSER_AUTH`                                        | private Composer repositories (`http-basic`, `bearer` and the other keys of the [COMPOSER_AUTH](https://getcomposer.org/doc/03-cli.md#composer-auth) format)                                                            |
| `app-client-id` and secret `app-private-key`                      | `composer install`, as `github-oauth` for `github.com` inside `COMPOSER_AUTH` | private Composer packages hosted on GitHub. The token reads, with `contents: read` only, the repositories of `app-repositories` (same owner as the calling repository), or only the calling repository when it is empty |
| job token, composite action with `github-packages-npm-scope` only | the registry access check and `npm ci`, as `NODE_AUTH_TOKEN`                  | npm packages of that scope on GitHub Packages. Without the scope, `npm ci` follows the URLs of `package-lock.json` with no token at all                                                                                 |

## Inputs of the reusable workflow

| Input              | Type    | Default                                     | Description                                                                                                                                         |
| ------------------ | ------- | ------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `path`             | string  | `.`                                         | Plugin directory (`composer.json`), relative to the checkout.                                                                                       |
| `php-versions`     | string  | `["8.3", "8.4"]`                            | JSON array of PHP versions, one job each.                                                                                                           |
| `php-extensions`   | string  | `mbstring, xml`                             | PHP extensions to install, separated by commas.                                                                                                     |
| `composer-scripts` | string  | `check:style, check:stan`                   | Composer scripts to run after `composer install`, separated by commas or new lines. Empty runs only the install.                                    |
| `app-client-id`    | string  | variable `WP_PLUGIN_CHECK_APP_CLIENT_ID`    | Client id of a GitHub App for private Composer packages on GitHub. Empty uses no App.                                                               |
| `app-repositories` | string  | variable `WP_PLUGIN_CHECK_APP_REPOSITORIES` | Repositories of the calling repository's owner that the App token reads, separated by commas or new lines. Empty reads the calling repository only. |
| `ui-directory`     | string  | empty                                       | UI directory (`package.json`, `package-lock.json`), relative to `path`. Empty skips the UI job.                                                     |
| `node-version`     | string  | `22`                                        | Node.js version of the UI job.                                                                                                                      |
| `npm-scripts`      | string  | `lint, build`                               | npm scripts to run after `npm ci`, separated by commas or new lines.                                                                                |
| `typecheck`        | boolean | `true`                                      | Run `tsc --noEmit` in the UI directory, with the TypeScript installed by `npm ci`.                                                                  |
| `dry-run`          | boolean | `false`                                     | Validate the inputs and the plugin files and report the commands only: no PHP or Node.js setup, no install, no credentials.                         |
| `timeout-minutes`  | number  | `20`                                        | Timeout of each job, in minutes.                                                                                                                    |

The composite action takes the same inputs, with `target` (`php` or `ui`, required) and `php-version` (one version, required for `php`) in place of `php-versions`, the default `ui` for `ui-directory`, empty `composer-scripts` and `npm-scripts`, and the secrets as the inputs `composer-auth` and `app-private-key`. It has no `timeout-minutes`. It also takes `github-packages-npm-scope` (see "UI with GitHub Packages").

## Secrets

| Secret            | Required                                      | Caller secret name                | Description                                                        |
| ----------------- | --------------------------------------------- | --------------------------------- | ------------------------------------------------------------------ |
| `composer-auth`   | no                                            | `WP_PLUGIN_CHECK_COMPOSER_AUTH`   | Composer credentials, a JSON object in the `COMPOSER_AUTH` format. |
| `app-private-key` | when `app-client-id` is set, unless `dry-run` | `WP_PLUGIN_CHECK_APP_PRIVATE_KEY` | Private key (PEM) of the GitHub App of `app-client-id`.            |

Pass each secret with one explicit line. Never `secrets: inherit`.

Example of `WP_PLUGIN_CHECK_COMPOSER_AUTH` for a private Composer repository with HTTP Basic authentication:

```json
{
  "http-basic": {
    "composer.example.com": { "username": "reader", "password": "..." }
  }
}
```

## Variables

| Variable                           | Fills              | Level                                    |
| ---------------------------------- | ------------------ | ---------------------------------------- |
| `WP_PLUGIN_CHECK_APP_CLIENT_ID`    | `app-client-id`    | repository or organization of the caller |
| `WP_PLUGIN_CHECK_APP_REPOSITORIES` | `app-repositories` | repository or organization of the caller |

An explicit `with:` input wins over the variables.

## Outputs

| Output            | Description                                                |
| ----------------- | ---------------------------------------------------------- |
| `php-commands`    | Number of commands of the PHP plan.                        |
| `php-plan-sha256` | SHA-256 of the PHP plan.                                   |
| `ui-commands`     | Number of commands of the UI plan; empty without a UI job. |
| `ui-plan-sha256`  | SHA-256 of the UI plan; empty without a UI job.            |

The plan has one line per command: the directory relative to the workspace, a tab, then the command and its arguments, each line ending with a new line. The plan does not depend on the PHP version. The composite action gives `commands` and `plan-sha256` for its target.

## Permissions

Grant the calling job `contents: read`. The reusable workflow asks for nothing more.

## UI with GitHub Packages

A UI whose `package-lock.json` resolves a scope from GitHub Packages (`https://npm.pkg.github.com`) needs the job token with `packages: read`, more than a component workflow grants. Run the UI check with the composite action in a job of your own instead, and leave `ui-directory` of the reusable workflow empty:

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
    - uses: <owner>/actions/wp-plugin-check@<sha40> # wp-plugin-check-v1.0.0
      with:
        target: ui
        ui-directory: ui
        npm-scripts: lint, build
        github-packages-npm-scope: "@owner"
```

With `github-packages-npm-scope`, the action first checks read access to every package of that scope that `package-lock.json` resolves from GitHub Packages, and reports every package without access in one run; then it gives the job token to `npm ci` only. Each package must grant the calling repository Read access (package settings, "Manage Actions access"). A GitHub App installation token is refused by that registry; the job token is the identity that works there.

## Example

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
    uses: <owner>/actions/.github/workflows/wp-plugin-check.yml@<sha40> # wp-plugin-check-v1.0.0
    with:
      php-versions: '["8.4"]'
      php-extensions: mbstring, xml, intl
      composer-scripts: check:style, check:stan, check:rector, test
      ui-directory: ui
    secrets:
      composer-auth: ${{ secrets.WP_PLUGIN_CHECK_COMPOSER_AUTH }}
      app-private-key: ${{ secrets.WP_PLUGIN_CHECK_APP_PRIVATE_KEY }}
```

Composite action, in a job of your own:

```yaml
steps:
  - uses: actions/checkout@<sha40> # vX.Y.Z
    with:
      persist-credentials: false
  - uses: <owner>/actions/wp-plugin-check@<sha40> # wp-plugin-check-v1.0.0
    with:
      target: php
      php-version: "8.4"
      composer-scripts: check:style, check:stan
```

Find the SHA of a release tag with `gh api repos/<owner>/actions/git/ref/tags/wp-plugin-check-vX.Y.Z`; when the object type is `tag`, dereference it with `gh api repos/<owner>/actions/git/tags/<object-sha>`.
