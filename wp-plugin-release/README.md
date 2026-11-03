# wp-plugin-release

Builds the distribution ZIP of a WordPress plugin and attaches it to the GitHub release of a tag. Each run:

1. validates every input before any build step (names, paths, exclude patterns, the Composer authentication JSON, the App credentials outside a dry run);
2. builds the UI in `<path>/ui` with `npm ci` and `npm run build` (only with `has-ui: true`) and checks that `ui-check-file` exists;
3. installs the production Composer dependencies (skipped when the plugin has no `composer.json`), with an optional Strauss pass, then runs the plugin's own `scripts/strip-lock-tokens.php` when it exists;
4. writes `<zip-name>-<tag-name>.zip` from the plugin tree, minus the excluded paths, and prints its file listing;
5. creates a GitHub App token narrowed to the calling repository and to `contents: write`, after every build step, and uploads the archive to the release of `tag-name`.

A dry run stops after step 4: no GitHub App token, no upload. The reusable workflow also passes no secret in a dry run and checks out the calling commit instead of the tag.

The component does not create the tag or the release (release-please, or the caller's pre-release job, does), and it triggers no other workflow or system. A caller that must notify another system after the upload (for example the rebuild of a Composer repository) does it in a separate job that `needs:` this one.

Two forms, one name:

| Form | Path | Use it when |
| --- | --- | --- |
| Reusable workflow | `.github/workflows/wp-plugin-release.yml` | the default: checks out the tag, one job, minimum permissions, concurrency, variable fallbacks |
| Composite action | `wp-plugin-release/action.yml` | the plugin tree is already in the job's workspace (for example, prepared by earlier steps), or the credentials come from a secret manager in the same job |

## What it replaces and deletes

- **Replaces** the asset named `<zip-name>-<tag-name>.zip` on the release of `tag-name` when one exists (`gh release upload --clobber`), so that a re-run of the same release ends with one archive. Other assets and the release itself are not touched.
- **Deletes nothing else.** It never deletes a release, a tag or another asset.
- In the workspace, the build writes what `npm ci`, `npm run build` and `composer install` write (`ui/node_modules`, build output, `vendor/`, `composer.lock`), as the plugin's own build would. The archive is written under `$RUNNER_TEMP`, outside the plugin tree.

## Archive contents

The archive holds every regular file under `path`, with paths relative to `path` at the root of the ZIP (no top-level folder), sorted, with a fixed timestamp: the same tree gives the same archive bytes.

Always excluded (patterns relative to `path`; `*` also matches `/`):

```
.git/*  .github/*  .cache/*  .claude/*  .aiox-core/*  tests/*  docs/*  docs-site/*
ui/src/*  ui/node_modules/*  node_modules/*
bitbucket-pipelines.yml  .php-cs-fixer.php  .phpstan.neon  .php-rector.php  .phpunit.xml
phpstan.neon  rector.php  auth.json  .release-please-manifest.json  release-please-config.json
```

`extra-zip-excludes` (separated by spaces or new lines) and `extra-zip-excludes-file` (one pattern per line, `#` starts a comment) add patterns. A pattern with `"`, `'`, `;`, `&`, `|`, `(`, `)`, `` ` ``, `$`, `<`, `>`, `\` or a control character fails the run. A symbolic link to a directory, a symbolic link out of `path` or an archive with no file fails the run.

## Values: input, repository variable, organization variable

Each value comes from the most specific level that sets it:

1. an explicit input in the caller's `with:`;
2. a variable of the calling repository;
3. a variable of the calling organization.

| Input | Variable | Without input and variable |
| --- | --- | --- |
| `app-client-id` | `WP_PLUGIN_RELEASE_APP_CLIENT_ID` | the run fails before the build, unless `dry-run` is true |

The secrets are stored as `WP_PLUGIN_RELEASE_APP_PRIVATE_KEY` and `WP_PLUGIN_RELEASE_COMPOSER_AUTH` and passed with one explicit line each (never `secrets: inherit`). The composite action reads no variable: it receives every value through `with:`.

## Inputs

"Both" means the reusable workflow and the composite action. The composite action takes booleans as the strings `true` and `false`.

| Input | Form | Required | Default | Rule |
| --- | --- | --- | --- | --- |
| `tag-name` | both | yes | | `^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$`, no `..`; the release must exist outside a dry run |
| `zip-name` | both | yes | | `^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`, no `..` |
| `path` | both | no | `.` | plugin root, relative to the workspace; never absolute, never `..` |
| `has-ui` | both | no | `false` | needs `<path>/ui/package.json` |
| `npm-github-packages-scope` | both | no | empty | npm scope such as `@example`, resolved from `https://npm.pkg.github.com` with the job token; needs `has-ui` |
| `ui-check-file` | both | no | `assets/dist/app.js` | relative to `path`; checked only with `has-ui` |
| `has-strauss` | both | no | `false` | full install, `composer strauss`, reinstall without dev dependencies and without scripts, `dump-autoload --optimize` |
| `node-version` | both | no | `22` | |
| `php-version` | both | no | `8.4` | |
| `php-extensions` | both | no | `mbstring, xml, json, curl` | as `shivammathur/setup-php` takes them; list every extension the plugin's Composer install needs |
| `extra-zip-excludes` | both | no | empty | see Archive contents |
| `extra-zip-excludes-file` | both | no | empty | relative to `path`; must be a regular file when set |
| `app-client-id` | both | unless dry run | variable (reusable) | client ID of the GitHub App |
| `dry-run` | both | no | `false` | build and list only |
| `timeout-minutes` | reusable | no | `20` | job timeout |
| `composer-auth` | composite | no | empty | JSON object in the `COMPOSER_AUTH` format, passed from a secret |
| `app-private-key` | composite | unless dry run | empty | passed from a secret |

## Secrets (reusable workflow)

| Secret | Caller secret | Required | Content |
| --- | --- | --- | --- |
| `app-private-key` | `WP_PLUGIN_RELEASE_APP_PRIVATE_KEY` | unless `dry-run` | private key of the GitHub App that uploads the archive |
| `composer-auth` | `WP_PLUGIN_RELEASE_COMPOSER_AUTH` | no | Composer authentication JSON object, for example `{"http-basic": {"<host>": {"username": "<user>", "password": "<password>"}}}`; store it without a trailing new line |

The component never prints either value. An invalid `composer-auth` fails the run with a message that does not show its content.

## Outputs

| Output | Form | Content |
| --- | --- | --- |
| `archive-name` | both | `<zip-name>-<tag-name>.zip` |
| `archive-sha256` | both | SHA-256 of the archive |
| `files` | both | number of files in the archive |
| `files-sha256` | both | SHA-256 of the sorted listing, one path per line with a final new line; the run log prints the listing |
| `archive-path` | composite | absolute path of the archive on the runner |

## Permissions

- The job needs `contents: read` only, for the checkout. With `npm-github-packages-scope`, the job token must also be able to read the scope's packages (grant the calling repository access to them in the package settings).
- The GitHub App needs the repository permission **Contents: read and write** on the calling repository, and must be installed on it. The token is created with `repositories: <calling repository>` and `permission-contents: write` only.

## Calling the reusable workflow

After release-please, in the release workflow of the plugin:

```yaml
jobs:
  release-please:
    # ... outputs release_created and tag_name

  release:
    needs: release-please
    if: ${{ needs.release-please.outputs.release_created == 'true' }}
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/wp-plugin-release.yml@<sha40> # wp-plugin-release-vX.Y.Z
    with:
      tag-name: ${{ needs.release-please.outputs.tag_name }}
      zip-name: example-plugin
      has-ui: true
      extra-zip-excludes: phpunit.xml .php-cs-fixer.dist.php
    secrets:
      app-private-key: ${{ secrets.WP_PLUGIN_RELEASE_APP_PRIVATE_KEY }}
      composer-auth: ${{ secrets.WP_PLUGIN_RELEASE_COMPOSER_AUTH }}

  # Optional: notify another system after the archive is on the release.
  notify:
    needs: release
    # ... a separate job of the caller; this component dispatches nothing.
```

A dry run in a pull request of the plugin builds the archive from the pull request commit without any secret:

```yaml
  release-dry-run:
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/wp-plugin-release.yml@<sha40> # wp-plugin-release-vX.Y.Z
    with:
      tag-name: 0.0.0-dry-run
      zip-name: example-plugin
      dry-run: true
```

A plugin whose Composer install needs `composer-auth` fails that dry run at `composer install`: the reusable workflow passes no secret in a dry run. Use the composite action with `dry-run: "true"` and `composer-auth` for that check.

## Calling the composite action

```yaml
jobs:
  release:
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    permissions:
      contents: read
    concurrency:
      group: wp-plugin-release-${{ github.repository }}-${{ inputs.tag }}
      cancel-in-progress: false
    steps:
      - uses: actions/checkout@<sha40> # vX.Y.Z
        with:
          ref: ${{ inputs.tag }}
          persist-credentials: false
      - uses: <owner>/actions/wp-plugin-release@<sha40> # wp-plugin-release-vX.Y.Z
        with:
          tag-name: ${{ inputs.tag }}
          zip-name: example-plugin
          composer-auth: ${{ secrets.WP_PLUGIN_RELEASE_COMPOSER_AUTH }}
          app-client-id: ${{ vars.WP_PLUGIN_RELEASE_APP_CLIENT_ID }}
          app-private-key: ${{ secrets.WP_PLUGIN_RELEASE_APP_PRIVATE_KEY }}
```

Find `<sha40>` with `gh api repos/<owner>/actions/git/ref/tags/wp-plugin-release-vX.Y.Z`; dereference an annotated tag with `gh api repos/<owner>/actions/git/tags/<object-sha>`. Never `@main` or a branch.

## Implementation notes

- The archive is written by `scripts/wp_plugin_release.py` (Python standard library, `zipfile`): files only, sorted, fixed timestamps, permissions kept without write bits for group and others. Exclude patterns use `fnmatch` on paths relative to `path`, the same matching as `zip -x` for these patterns.
- `setup-node` runs with `package-manager-cache: false`: a release build does not restore a dependency cache.
- The Composer authentication reaches Composer through the `COMPOSER_AUTH` environment variable of one step; no `auth.json` is written in the plugin tree.
