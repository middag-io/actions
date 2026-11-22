# moodle-plugin-check

Validates a Moodle plugin in a change. Publishes nothing.

A composite action, `moodle-plugin-check/action.yml`: it checks one PHP version in a job of the caller, on a checkout the caller made. Several PHP versions are a job matrix of the caller.

## What it does

1. Validates the inputs (`app-repositories` is required with `app-client-id`, and `app-private-key` outside a dry run) and the plugin metadata, before anything else:
   - `version.php` exists at `path`, starts with `<?php` and sets `$plugin->component` and `$plugin->version` once each, as literal values; the check reads only `component`, `version`, `requires`, `maturity` and `release`, and leaves other fields (for example a `$plugin->supported` or `$plugin->dependencies` array) alone;
   - `$plugin->component` is a frankenstyle name (`<type>_<name>`) and, when the input `component` is set, equals it;
   - `$plugin->version` (and `$plugin->requires`, when set) is `YYYYMMDDXX` with a valid date, optionally followed by a decimal part;
   - `$plugin->maturity`, when set, is one of `MATURITY_ALPHA`, `MATURITY_BETA`, `MATURITY_RC`, `MATURITY_STABLE`;
   - the English language file exists: `lang/en/<name>.php` for an activity module (`mod_<name>`), `lang/en/<component>.php` for every other plugin type.
2. Plans the Composer commands, when the plugin has a `composer.json`: `composer validate --no-interaction` (unless `composer-validate` is false), `composer install --no-interaction <composer-options>`, then `composer run-script <name>` for each name in `composer-scripts`, in order. Without a `composer.json`, only step 1 runs, and a non-empty `composer-scripts` fails.
3. Outside a dry run: mints a read-only token of a GitHub App when `app-client-id` is set, sets up PHP with Composer 2 and the extensions through the [`php-composer-setup`](../php-composer-setup/README.md) action of the same release (only for a plugin with `composer.json`; that action does not install here), and runs the planned commands in the plugin directory. The first failing command fails the job.

4. For a plugin with `composer.json`, audits the installed dependencies through the [`php-composer-scan`](../php-composer-scan/README.md) action of the same release: an advisory whose severity is in `audit-blocking-severities` fails the job; lower severities and abandoned packages are warnings. No input turns the audit off; an advisory the plugin does not remediate goes to `config.audit.ignore` of its `composer.json`, with the reason. In a dry run, `php-composer-scan` only validates and prints its command.

The commands run without a shell, as argument lists. `composer-options` takes long options only (`--name` or `--name=value`), and `composer-scripts` takes script names only, so an input cannot add a command.

`php-composer-setup` writes the App token to the global Composer `auth.json` of the runner (`github-oauth` for `github.com`, mode `0600`, outside the checkout), where the planned Composer commands read it. It stays there until the end of the job, so the Composer scripts and the commands they run can read it too. It is never written to the checkout or to the git configuration.

The token is a `github-oauth` credential for `github.com` only. Composer uses it for the GitHub API, for the archives of GitHub packages and for its own clones of GitHub repositories. A Composer repository on another host that needs a login (for example a private Satis or Packagist with `http-basic`) gets no credential from this component, and neither do `git` commands run by a Composer script.

In a dry run (`dry-run: true`), only steps 1 and 2 run, and `php-composer-setup` and `php-composer-scan` run as dry runs: no credentials, no PHP set-up, no Composer run, no audit. The job prints the commands it would run.

## What it deletes

Nothing. The component writes no file in the repository and publishes nothing. `composer install` writes `vendor/` in the checkout of the job, which ends with the job. With an App token, `php-composer-setup` writes the global Composer `auth.json` of the runner, outside the checkout.

## Inputs

| Input | Default | Meaning |
| --- | --- | --- |
| `path` | `.` | Plugin directory (the one that holds `version.php`), relative to the checkout. A path that leaves the checkout fails. |
| `component` | empty | Expected frankenstyle name. Empty skips the comparison with `version.php`. |
| `php-version` | — | PHP version of this run (for example `8.3`). |
| `php-extensions` | `soap, xml, curl, mbstring, bcmath, zip, gd, intl` | PHP extensions, separated by commas. |
| `composer-validate` | `true` | Run `composer validate` when the plugin has a `composer.json`. |
| `composer-options` | `--prefer-dist --optimize-autoloader` | Extra long options of `composer install`, separated by spaces. |
| `composer-scripts` | empty | Composer scripts to run in order, separated by commas or new lines (for example `check:style, check:stan, test`). |
| `app-client-id` | empty | Client id of a GitHub App that reads private Composer dependencies on GitHub. Empty: public dependencies only. |
| `app-repositories` | empty | Repositories of the App owner that the token reaches (the private dependencies), names without owner, separated by commas or new lines. Required with `app-client-id`, so that the token never reaches every repository of the installation. |
| `app-owner` | empty | Owner whose installation of the App mints the token. Empty: the owner of the calling repository. |
| `app-private-key` | empty | Private key of the App. Required with `app-client-id` outside a dry run. |
| `audit-blocking-severities` | `critical` | Severities of a dependency advisory that fail the job, separated by commas: any of `critical`, `high`, `medium`, `low`. |
| `dry-run` | `false` | Validate and plan only. |

## Outputs

| Output | Meaning |
| --- | --- |
| `component` | Frankenstyle name read from `version.php`. |
| `version` | `$plugin->version` read from `version.php`. |
| `commands` | JSON array of the planned Composer commands, in order (empty array without `composer.json`). |
| `release` | `$plugin->release`, empty when not set. |
| `maturity` | `$plugin->maturity`, empty when not set. |

## Variables and secrets

The action reads no variable and no secret by itself. The caller passes them in `with:`, one explicit line each:

| Name | Kind | Passed to |
| --- | --- | --- |
| `MOODLE_PLUGIN_CHECK_APP_CLIENT_ID` | variable | `app-client-id` |
| `MOODLE_PLUGIN_CHECK_APP_REPOSITORIES` | variable | `app-repositories` |
| `MOODLE_PLUGIN_CHECK_AUDIT_BLOCKING_SEVERITIES` | variable | `audit-blocking-severities` |
| `MOODLE_PLUGIN_CHECK_APP_PRIVATE_KEY` | secret | `app-private-key` |

The App needs only read access to the contents of the repositories of the private dependencies. The token it mints here asks for `contents: read` only, on the repositories of `app-repositories` only.

## Permissions

The caller grants `contents: read` to the calling job. The component asks for nothing more.

## Example

Reference the component by its major tag `moodle-plugin-check-v1`. A fork and a caller outside the maintainer organization pin the full commit SHA of a release tag instead, with the tag as a comment (`@<sha40> # moodle-plugin-check-vX.Y.Z`). Find the SHA with `gh api repos/<owner>/actions/git/ref/tags/moodle-plugin-check-vX.Y.Z` (dereference an annotated tag).

```yaml
steps:
  - uses: actions/checkout@<sha40> # vX.Y.Z
    with:
      persist-credentials: false
  - uses: <owner>/actions/moodle-plugin-check@moodle-plugin-check-v1
    with:
      php-version: "8.3"
      component: local_example
      composer-scripts: |
        check:style
        check:stan
        test
      app-client-id: ${{ vars.MOODLE_PLUGIN_CHECK_APP_CLIENT_ID }}
      app-repositories: ${{ vars.MOODLE_PLUGIN_CHECK_APP_REPOSITORIES }}
      app-private-key: ${{ secrets.MOODLE_PLUGIN_CHECK_APP_PRIVATE_KEY }}
```

A pull request from a fork gets no secrets: leave `app-client-id` empty there, or the check fails with a message that names `app-private-key`.
