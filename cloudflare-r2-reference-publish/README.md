# cloudflare-r2-reference-publish

Generates the reference of a project with phpDocumentor (PHP) or TypeDoc (JavaScript and TypeScript) and publishes it to a Cloudflare R2 bucket, or to any bucket behind an S3-compatible API, as the immutable kind `xref` of a version. Each run:

1. checks out the calling repository at the release ref and validates every input, resolving the version from the input `version` or from the tag;
2. generates the reference into `<output-path>/xref/<version>/` and writes `<output-path>/manifest.json`, in a job that holds no secret: the generator runs the caller's code and dependencies;
3. uploads that package with the composite action of [`cloudflare-r2-docs-publish`](../cloudflare-r2-docs-publish/README.md) (`kinds: xref`) in a second job, the only one that receives the secrets.

A dry run generates the reference and lists the object keys; no credentials, no endpoint, no upload.

The upload, its immutability, its conditional writes and what it overwrites are those of `cloudflare-r2-docs-publish`: read its README. This component adds the generation.

Two forms, one name:

| Form | Path | Use it when |
| --- | --- | --- |
| Reusable workflow | `.github/workflows/cloudflare-r2-reference-publish.yml` | the default: generation and upload, two jobs, minimum permissions, concurrency, variable fallbacks |
| Composite action | `cloudflare-r2-reference-publish/action.yml` | generation only, in a job of the caller: the caller then uploads the package itself, for example together with its `docs` and `schemas` (see "One publication per version") |

## Generators

| `generator` | Runs | Input `config` | Input `source-paths` |
| --- | --- | --- | --- |
| `phpdocumentor` | the official image `phpdoc/phpdoc:3.10.0`, pinned by digest (multi-architecture, amd64 and arm64), with `docker run --network=none`, as the runner user | `--config=<file>`; empty: phpDocumentor reads `phpdoc.dist.xml` or `phpdoc.xml` in `working-directory` when present | one `--directory=<path>` each; empty: the configuration decides |
| `typedoc` | `npm ci` from the project's `package-lock.json`, then `npx --no-install typedoc` | `--options <file>`; empty: TypeDoc reads `typedoc.json` and the other files it looks up | entry points; empty: the configuration decides |

- The output directory is always forced to `<output-path>/xref/<version>/`; a `target`/`out` in the configuration is overridden.
- TypeDoc must be a dependency of the project (in `package-lock.json`): the component never downloads a TypeDoc that the lockfile does not pin. A project without `package-lock.json` fails the run.
- The run fails when the generator wrote no `index.html`.
- Doctum and Sami are not supported.

## Version

The version is normalized to `v<MAJOR>.<MINOR>.<PATCH>[-<pre>]`, the form of the storage key (`<package-name>/xref/v1.4.0/...`). It comes from, in order:

1. the input `version` (`1.4.0` and `v1.4.0` both give `v1.4.0`);
2. the input `ref`, when it is a tag;
3. the tag that triggered the run.

A tag is read as `1.4.0`, `v1.4.0` or `<component>-v1.4.0` (the per-component tags of release-please). Anything else, a branch name included, fails the run: set `version`.

## Package and manifest

```
<output-path>/
  manifest.json        {"package": "<package-name>", "latest": "<version>", "versions": {"<version>": {"has_reference": true}}}
  xref/<version>/...   the generated reference
```

With `write-manifest: true` (the default) the upload replaces `<package-name>/manifest.json` with that file, after every version object (the pointer to the latest version). Set `write-manifest: false` when another publication of the package writes a richer manifest.

Every object gets its `Content-Type` from the file extension through `cloudflare-r2-docs-publish` (`text/html; charset=utf-8` for `.html`, `text/css`, `text/javascript`, `image/svg+xml`, `image/png`, `image/x-icon`, and so on): the extensions that both generators write are all in its table.

## One publication per version

`cloudflare-r2-docs-publish` refuses a version whose prefix of any kind it checks already holds an object: a published version never gains a kind later. This component checks and writes only `xref`. A package that also publishes `docs` or `schemas` for the same version:

- either publishes them in its own run with `kinds` narrowed to those kinds (for example `kinds: docs,schemas`), never the default `docs,xref,schemas`, which would fail on the `xref` prefix written here;
- or runs the composite action of this component in its own docs build job, adds its `docs` and `schemas` to the same package and uploads everything once with `cloudflare-r2-docs-publish`.

## Values: input, repository variable, organization variable

Each value comes from the most specific level that sets it: an explicit input, a variable of the calling repository, a variable of the calling organization.

| Input | Variable | Without input and variable |
| --- | --- | --- |
| `bucket` | `CLOUDFLARE_R2_DOCS_PUBLISH_BUCKET`, the variable of `cloudflare-r2-docs-publish` | the run fails before any write |

A repository that already publishes its docs with `cloudflare-r2-docs-publish` needs no new variable and no new secret. The endpoint is a secret, never an input or a variable, because it holds the account id. Store the three secrets as `CLOUDFLARE_R2_DOCS_PUBLISH_ENDPOINT`, `CLOUDFLARE_R2_DOCS_PUBLISH_ACCESS_KEY_ID` and `CLOUDFLARE_R2_DOCS_PUBLISH_SECRET_ACCESS_KEY` and pass each with one explicit line (never `secrets: inherit`).

The composite action reads no variable.

## Inputs

"Both" means the reusable workflow and the composite action.

| Input | Form | Required | Default | Rule |
| --- | --- | --- | --- | --- |
| `generator` | both | yes | | `phpdocumentor` or `typedoc` |
| `package-name` | both | no | calling repository name | `^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$` |
| `version` | both | no | from the tag | see "Version" |
| `ref` | reusable | no | ref of the run | ref of the calling repository to check out; a tag also gives the version |
| `ref-name` | composite | no | the tag of the run | tag the version is read from |
| `working-directory` | both | no | `.` | project directory, relative; never absolute, never `..` |
| `source-paths` | both | no | empty | paths relative to `working-directory`, separated by commas or new lines; each must exist |
| `config` | both | no | empty | file relative to `working-directory`; must exist |
| `output-path` | both | no | `.docs-dist` | package directory, relative; absent or empty |
| `node-version-file` | reusable | no | empty | `typedoc` only: file for `actions/setup-node` (for example `.node-version`); empty sets up the latest Node.js LTS |
| `bucket` | reusable | yes | variable | see "Values" |
| `write-manifest` | reusable | no | `true` | write `<package-name>/manifest.json` after the version objects |
| `dry-run` | reusable | no | `false` | generate and list only |
| `timeout-minutes` | reusable | no | `20` | timeout of each job |
| `runner` | reusable | no | `ubuntu-24.04` | runner label of both jobs; the runner needs Docker for `phpdocumentor` |

## Secrets (reusable workflow)

| Secret | Required | Content |
| --- | --- | --- |
| `endpoint` | unless `dry-run` | S3 API endpoint, `https://<host>` |
| `access-key-id` | unless `dry-run` | S3 access key id with object read and write on the bucket |
| `secret-access-key` | unless `dry-run` | S3 secret access key |

## Outputs

| Output | Form | Content |
| --- | --- | --- |
| `version` | both | the normalized version |
| `objects` | reusable | number of version objects, `manifest.json` excluded |
| `payload-sha256` | reusable | SHA-256 of the object listing, as in `cloudflare-r2-docs-publish` |
| `package-path` | composite | package directory, relative to the workspace |
| `xref-path` | composite | directory of the generated reference |
| `files` | composite | number of files the generator wrote |

## Permissions

Both jobs need `contents: read` only.

## Concurrency

The `publish` job uses the concurrency group of `cloudflare-r2-docs-publish` (`cloudflare-r2-docs-publish-<package-name>-publish`): a reference and a docs publication of the same package never run at the same time, so their `manifest.json` writes never cross. A running publication is never cancelled; a dry run has its own group per run.

## Calling the reusable workflow

Run it on the release tag. Reference the component by its major tag `cloudflare-r2-reference-publish-v1`. A fork and a caller outside the maintainer organization pin the full commit SHA of a release tag instead, with the tag as a comment (`@<sha40> # cloudflare-r2-reference-publish-vX.Y.Z`).

PHP library, released by release-please:

```yaml
on:
  push:
    tags: ["v*"]

permissions: {}

jobs:
  reference:
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/cloudflare-r2-reference-publish.yml@cloudflare-r2-reference-publish-v1
    with:
      generator: phpdocumentor
      source-paths: src
    secrets:
      endpoint: ${{ secrets.CLOUDFLARE_R2_DOCS_PUBLISH_ENDPOINT }}
      access-key-id: ${{ secrets.CLOUDFLARE_R2_DOCS_PUBLISH_ACCESS_KEY_ID }}
      secret-access-key: ${{ secrets.CLOUDFLARE_R2_DOCS_PUBLISH_SECRET_ACCESS_KEY }}
```

TypeScript library, from the job that created the release (the tag is an output of the release job):

```yaml
  reference:
    needs: release
    if: ${{ needs.release.outputs.release-created == 'true' }}
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/cloudflare-r2-reference-publish.yml@cloudflare-r2-reference-publish-v1
    with:
      generator: typedoc
      ref: ${{ needs.release.outputs.tag-name }}
      config: typedoc.json
      node-version-file: .node-version
    secrets:
      endpoint: ${{ secrets.CLOUDFLARE_R2_DOCS_PUBLISH_ENDPOINT }}
      access-key-id: ${{ secrets.CLOUDFLARE_R2_DOCS_PUBLISH_ACCESS_KEY_ID }}
      secret-access-key: ${{ secrets.CLOUDFLARE_R2_DOCS_PUBLISH_SECRET_ACCESS_KEY }}
```

## Calling the composite action

Generation only, in a build job of the caller; the caller uploads the package.

```yaml
      - uses: actions/checkout@<sha40> # vX.Y.Z
        with:
          persist-credentials: false
      - id: reference
        uses: <owner>/actions/cloudflare-r2-reference-publish@cloudflare-r2-reference-publish-v1
        with:
          generator: phpdocumentor
          source-paths: src
      - uses: actions/upload-artifact@<sha40> # vX.Y.Z
        with:
          name: docs-package
          path: ${{ steps.reference.outputs.package-path }}
          include-hidden-files: true
```
