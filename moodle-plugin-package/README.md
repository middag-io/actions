# moodle-plugin-package

Packages a Moodle plugin tree as an installable ZIP. Each run:

1. validates every input and `version.php` at the root of the plugin tree: `$plugin->version` must be a `YYYYMMDDXX` number, `$plugin->release` a non-empty string, and `$plugin->component`, when set, must equal `zip-name`. The file is read, never executed;
2. installs the Composer dependencies when `composer.json` exists: PHP, Composer and `composer-auth` come from the [`php-composer-setup`](../php-composer-setup/README.md) action of the same release, then `composer install` with `composer-options` runs in the plugin tree;
3. when `npm-build-command` is set: sets up Node, checks that the token reads every dependency that `package-lock.json` resolves from the GitHub Packages npm registry (all refusals reported at once), runs `npm ci` (or `npm install`, with a warning, without a lockfile) and `npm run <npm-build-command>`;
4. copies the tree with `rsync`, minus the default excludes and the exclude file, and writes `<zip-name>-<tag-name>.zip` (or `<zip-name>-<short sha>.zip` without a tag) with every file under `<zip-name>/`;
5. uploads the ZIP as a workflow artifact named after the file;
6. with `upload-release-asset: true`, uploads the ZIP to the existing GitHub release of `tag-name`.

A dry run runs steps 1 and 4 only, and `php-composer-setup` as a dry run when `composer.json` exists: no dependency install, no credentials, no artifact, no release upload. Its outputs describe the ZIP that a real run of the same tree would build, before any dependency install.

The ZIP is deterministic: entries sorted by path, a fixed entry date, mode `0755` for executable files and `0644` for the others. It holds file entries only, no directory entries, so an empty directory does not ship. Symbolic links are replaced by the files they point to; a dangling link fails the run.

Two forms, one name:

| Form | Path | Use it when |
| --- | --- | --- |
| Reusable workflow | `.github/workflows/moodle-plugin-package.yml` | the ZIP goes to a workflow artifact only, and the dependencies are public or come from a Composer repository behind `composer-auth`; one job with `contents: read` |
| Composite action | `moodle-plugin-package/action.yml` | the ZIP also goes to the GitHub release of the tag, or `npm-build-command` reads private packages from GitHub Packages: both need more than `contents: read`, which the reusable workflow does not grant |

## Exclude rules

The copy uses `rsync -a --copy-links` with these `--exclude` patterns, then `--exclude-from <dist-excludes-file>` when that file exists:

```
.git/  .github/  .cache/  .claude/  .aiox-core/  dist/  node_modules/
vendor/bin/.phpunit/  tests/  docs-site/  .php-cs-fixer.cache  .phpunit.cache/
bitbucket-pipelines.yml  auth.json  composer.phar
```

rsync semantics apply: a pattern without an inner `/` matches at any depth (`tests/` also drops `vendor/<package>/tests/`), a leading `/` anchors it at the root of the plugin tree, a trailing `/` matches directories only. rsync does not read `.gitattributes` `export-ignore`: list every path that must not ship in the exclude file.

`dist-excludes-file` defaults to `.distignore`. A missing `.distignore` is ignored. Any other value names a file that must exist: a misspelled exclude file fails the run instead of shipping what it should have dropped.

## What it overwrites and deletes

- **Deletes** one thing only: with `upload-release-asset: true`, the asset of the release of `tag-name` that has the same file name as the new ZIP. `gh release upload --clobber` deletes it, then uploads the new ZIP. If the upload then fails, the release has no ZIP until a re-run. The release must exist: the component does not create it.
- Deletes nothing else in the repository, in the releases or in the registries.
- Builds in a new directory under `$RUNNER_TEMP`; the workspace changes only through the Composer and npm installs and the npm build.
- With `composer-auth`, `php-composer-setup` writes the credentials to the global Composer `auth.json` of the runner (mode `0600`, outside the plugin tree), where they stay until the end of the job; the npm install and build that follow can read that file.

## Inputs

"Both" means the reusable workflow and the composite action.

| Input | Form | Required | Default | Rule |
| --- | --- | --- | --- | --- |
| `zip-name` | both | yes | | Moodle frankenstyle name, `^[a-z][a-z0-9]*_[a-z][a-z0-9_]*$` (for example `local_example`); equals `$plugin->component` when that is set |
| `tag-name` | both | no | empty | `^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$`; names the archive. Reusable: also the ref it checks out (empty: the triggering ref) |
| `path` | both | no | `.` | plugin tree with `version.php` at its root, relative to the workspace; never absolute, never `..` |
| `php-version` | both | no | `8.4` | PHP for the Composer install |
| `php-extensions` | both | no | `soap, xml, curl, mbstring, bcmath, zip, gd, intl` | |
| `composer-options` | both | no | `--no-dev --prefer-dist --optimize-autoloader --no-interaction` | split on spaces |
| `dist-excludes-file` | both | no | `.distignore` | relative to `path`; see Exclude rules |
| `npm-build-command` | both | no | empty | npm script name, `^[A-Za-z0-9][A-Za-z0-9:._/-]{0,127}$`; empty skips Node |
| `npm-working-dir` | both | no | `.` | relative to `path`; must hold `package.json` |
| `node-version` | both | no | `22` | |
| `retention-days` | both | no | `30` | retention of the workflow artifact |
| `dry-run` | both | no | `false` | validate and build the ZIP only |
| `timeout-minutes` | reusable | no | `20` | job timeout |
| `composer-auth` | composite | no | empty | `COMPOSER_AUTH` JSON, passed from a secret; `php-composer-setup` writes it to the global Composer `auth.json`. A `COMPOSER_AUTH` that the job environment already sets still applies |
| `github-token` | composite | no | the job token | token for the GitHub Packages npm registry and for the release upload |
| `upload-release-asset` | composite | no | `false` | upload to the release of `tag-name`; needs `tag-name` |

## Secrets (reusable workflow)

| Secret | Required | Content |
| --- | --- | --- |
| `composer-auth` | no | `COMPOSER_AUTH` JSON for private Composer repositories |

Store it as `MOODLE_PLUGIN_PACKAGE_COMPOSER_AUTH` and pass it with one explicit line (never `secrets: inherit`). A dry run does not pass it to the action.

## Outputs

| Output | Content |
| --- | --- |
| `archive-name` | file name of the ZIP, also the name of the workflow artifact |
| `version` | `$plugin->version` from `version.php` |
| `release` | `$plugin->release` from `version.php` |
| `files` | number of files in the ZIP |
| `listing-sha256` | SHA-256 of the listing `<sha256>  <zip-name>/<path>` (two spaces, one line per file, sorted by path). The run log prints the listing; `sha256sum` of a saved copy gives the same value |
| `archive-path` | composite only: path of the ZIP on the runner |

## Permissions

- Reusable workflow: `contents: read`. The caller's job grants at least that.
- Composite action: `contents: read`, plus `contents: write` with `upload-release-asset: true`, plus `packages: read` when `npm-build-command` installs packages from GitHub Packages with the job token. Each such package must also grant the calling repository read access. Pass another token in `github-token` to use other credentials.

## Calling the reusable workflow

Reference the component by its major tag `moodle-plugin-package-v1`. A fork and a caller outside the maintainer organization pin the full commit SHA of a release tag instead, with the tag as a comment (`@<sha40> # moodle-plugin-package-vX.Y.Z`). Find the SHA with `gh api repos/<owner>/actions/git/ref/tags/moodle-plugin-package-vX.Y.Z` (dereference an annotated tag).

```yaml
jobs:
  package:
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/moodle-plugin-package.yml@moodle-plugin-package-v1
    with:
      zip-name: local_example
      tag-name: ${{ needs.release.outputs.tag_name }}
      php-version: "8.3"
    secrets:
      composer-auth: ${{ secrets.MOODLE_PLUGIN_PACKAGE_COMPOSER_AUTH }}
```

The ZIP is in the workflow artifact named by the `archive-name` output.

## Calling the composite action

```yaml
jobs:
  package:
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    permissions:
      contents: write   # upload the ZIP to the release
      packages: read    # npm dependencies from GitHub Packages
    concurrency:
      group: package-${{ github.repository }}-${{ needs.release.outputs.tag_name }}
      cancel-in-progress: false
    steps:
      - uses: actions/checkout@<sha40> # vX.Y.Z
        with:
          ref: ${{ needs.release.outputs.tag_name }}
          persist-credentials: false
      - uses: <owner>/actions/moodle-plugin-package@moodle-plugin-package-v1
        with:
          zip-name: local_example
          tag-name: ${{ needs.release.outputs.tag_name }}
          php-version: "8.3"
          dist-excludes-file: config/dist-excludes.release
          npm-build-command: build
          npm-working-dir: ui
          upload-release-asset: "true"
          composer-auth: ${{ secrets.MOODLE_PLUGIN_PACKAGE_COMPOSER_AUTH }}
```

## Implementation notes

- `scripts/package.py` (Python standard library) validates the inputs, reads `version.php`, runs `rsync`, writes the ZIP and the listing, and reads `package-lock.json` (lockfile versions 1 to 3) for the registry check. The tests in `tests/` run without network; the build tests need `rsync`.
- The release upload uses the GitHub CLI of the runner image (`gh release upload`), not a third-party action.
