# moodle-plugin-check

Validates a Moodle plugin in a change. Publishes nothing.

Two forms, one name:

- reusable workflow `.github/workflows/moodle-plugin-check.yml`: checks out the calling repository and runs one job per PHP version;
- composite action `moodle-plugin-check/action.yml`: checks one PHP version in a job of the caller, on a checkout the caller made.

## What it does

1. Validates the inputs (`app-repositories` is required with `app-client-id`, and `app-private-key` outside a dry run) and the plugin metadata, before anything else:
   - `version.php` exists at `path`, starts with `<?php` and sets `$plugin->component` and `$plugin->version` once each, as literal values; the check reads only `component`, `version`, `requires`, `maturity` and `release`, and leaves other fields (for example a `$plugin->supported` or `$plugin->dependencies` array) alone;
   - `$plugin->component` is a frankenstyle name (`<type>_<name>`) and, when the input `component` is set, equals it;
   - `$plugin->version` (and `$plugin->requires`, when set) is `YYYYMMDDXX` with a valid date, optionally followed by a decimal part;
   - `$plugin->maturity`, when set, is one of `MATURITY_ALPHA`, `MATURITY_BETA`, `MATURITY_RC`, `MATURITY_STABLE`;
   - the English language file exists: `lang/en/<name>.php` for an activity module (`mod_<name>`), `lang/en/<component>.php` for every other plugin type.
2. Plans the Composer commands, when the plugin has a `composer.json`: `composer validate --no-interaction` (unless `composer-validate` is false), `composer install --no-interaction <composer-options>`, then `composer run-script <name>` for each name in `composer-scripts`, in order. Without a `composer.json`, only step 1 runs, and a non-empty `composer-scripts` fails.
3. Outside a dry run: mints a read-only token of a GitHub App when `app-client-id` is set, sets up PHP with Composer 2 and the extensions through the [`php-composer-setup`](../php-composer-setup/README.md) action of the same release (only for a plugin with `composer.json`; that action does not install here), and runs the planned commands in the plugin directory. The first failing command fails the job.

The commands run without a shell, as argument lists. `composer-options` takes long options only (`--name` or `--name=value`), and `composer-scripts` takes script names only, so an input cannot add a command.

`php-composer-setup` writes the App token to the global Composer `auth.json` of the runner (`github-oauth` for `github.com`, mode `0600`, outside the checkout), where the planned Composer commands read it. It stays there until the end of the job, so the Composer scripts and the commands they run can read it too. It is never written to the checkout or to the git configuration.

The token is a `github-oauth` credential for `github.com` only. Composer uses it for the GitHub API, for the archives of GitHub packages and for its own clones of GitHub repositories. A Composer repository on another host that needs a login (for example a private Satis or Packagist with `http-basic`) gets no credential from this component, and neither do `git` commands run by a Composer script.

In a dry run (`dry-run: true`), only steps 1 and 2 run, and `php-composer-setup` runs as a dry run: no credentials, no PHP set-up, no Composer run. The job prints the commands it would run.

## What it deletes

Nothing. The component writes no file in the repository and publishes nothing. `composer install` writes `vendor/` in the checkout of the job, which ends with the job. With an App token, `php-composer-setup` writes the global Composer `auth.json` of the runner, outside the checkout.

## Inputs

| Input | Workflow | Action | Default | Meaning |
| --- | --- | --- | --- | --- |
| `path` | yes | yes | `.` | Plugin directory (the one that holds `version.php`), relative to the checkout. A path that leaves the checkout fails. |
| `component` | yes | yes | empty | Expected frankenstyle name. Empty skips the comparison with `version.php`. |
| `php-versions` | yes | no | `["8.2", "8.3", "8.4"]` | JSON array of PHP versions; one job each. |
| `php-version` | no | yes (required) | — | PHP version of this run (for example `8.3`). |
| `php-extensions` | yes | yes | `soap, xml, curl, mbstring, bcmath, zip, gd, intl` | PHP extensions, separated by commas. |
| `composer-validate` | yes (boolean) | yes (`true`/`false`) | `true` | Run `composer validate` when the plugin has a `composer.json`. |
| `composer-options` | yes | yes | `--prefer-dist --optimize-autoloader` | Extra long options of `composer install`, separated by spaces. |
| `composer-scripts` | yes | yes | empty | Composer scripts to run in order, separated by commas or new lines (for example `check:style, check:stan, test`). |
| `app-client-id` | yes | yes | variable `MOODLE_PLUGIN_CHECK_APP_CLIENT_ID` (workflow); empty (action) | Client id of a GitHub App that reads private Composer dependencies on GitHub. Empty: public dependencies only. |
| `app-repositories` | yes | yes | variable `MOODLE_PLUGIN_CHECK_APP_REPOSITORIES` (workflow); empty (action) | Repositories of the App owner that the token reaches (the private dependencies), names without owner, separated by commas or new lines. Required with `app-client-id`, so that the token never reaches every repository of the installation. |
| `app-owner` | yes | yes | empty | Owner whose installation of the App mints the token. Empty: the owner of the calling repository. |
| `app-private-key` | secret | input | empty | Private key of the App. Required with `app-client-id` outside a dry run. |
| `dry-run` | yes (boolean) | yes (`true`/`false`) | `false` | Validate and plan only. |
| `timeout-minutes` | yes | no | `20` | Timeout of each job. |

## Outputs

| Output | Meaning |
| --- | --- |
| `component` | Frankenstyle name read from `version.php`. |
| `version` | `$plugin->version` read from `version.php`. |
| `commands` | JSON array of the planned Composer commands, in order (empty array without `composer.json`). |
| `release` | Action only: `$plugin->release`, empty when not set. |
| `maturity` | Action only: `$plugin->maturity`, empty when not set. |

Every job of the matrix reads the same `version.php` and plans the same commands, so the workflow outputs do not depend on the PHP version.

## Variables and secrets

Most specific first: explicit input, then a variable of the calling repository, then a variable of the calling organization.

| Name | Kind | Read by |
| --- | --- | --- |
| `MOODLE_PLUGIN_CHECK_APP_CLIENT_ID` | variable | default of `app-client-id` in the reusable workflow |
| `MOODLE_PLUGIN_CHECK_APP_REPOSITORIES` | variable | default of `app-repositories` in the reusable workflow |
| `MOODLE_PLUGIN_CHECK_APP_PRIVATE_KEY` | secret | passed by the caller to `app-private-key`, one explicit line |

The App needs only read access to the contents of the repositories of the private dependencies. The token it mints here asks for `contents: read` only, on the repositories of `app-repositories` only.

## Permissions

The caller grants `contents: read` to the calling job. The component asks for nothing more.

## Example

Reference the component by the full commit SHA of a release tag of this component, with the tag as a comment. Find the SHA with `gh api repos/<owner>/actions/git/ref/tags/moodle-plugin-check-vX.Y.Z` (dereference an annotated tag).

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

permissions: {}

jobs:
  moodle-plugin-check:
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/moodle-plugin-check.yml@<sha40> # moodle-plugin-check-vX.Y.Z
    with:
      component: local_example
      php-versions: '["8.2", "8.3"]'
      composer-scripts: |
        check:style
        check:stan
        test
    secrets:
      app-private-key: ${{ secrets.MOODLE_PLUGIN_CHECK_APP_PRIVATE_KEY }}
```

The composite action in a job of the caller:

```yaml
steps:
  - uses: actions/checkout@<sha40> # vX.Y.Z
    with:
      persist-credentials: false
  - uses: <owner>/actions/moodle-plugin-check@<sha40> # moodle-plugin-check-vX.Y.Z
    with:
      php-version: "8.3"
      composer-scripts: test
```

A pull request from a fork gets no secrets: leave `app-client-id` empty there, or the check fails with a message that names `app-private-key`.
