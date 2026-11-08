# cloudflare-r2-docs-publish

Publishes a prepared documentation package to a Cloudflare R2 bucket, or to any bucket behind an S3-compatible API, as an immutable version. Each run:

1. validates every input and the package directory, and lists the object keys (no network);
2. refuses the run when the `<package-name>/<kind>/<version>/` prefix of any allowed kind (present in the package or not) already holds an object;
3. writes every file of the version with a conditional PUT (`If-None-Match: *`): the store refuses to replace an existing key;
4. writes `<package-name>/manifest.json` last, after every version object succeeded (skipped with `write-manifest: false`).

A dry run stops after step 1: no credentials, no endpoint, no network.

The component does not build the package. The caller builds it (Markdown, generated reference, schemas, manifest) and hands over the directory.

Two forms, one name:

| Form | Path | Use it when |
| --- | --- | --- |
| Reusable workflow | `.github/workflows/cloudflare-r2-docs-publish.yml` | the default: the package comes from a workflow artifact of the same run; one job, minimum permissions, concurrency, variable fallbacks |
| Composite action | `cloudflare-r2-docs-publish/action.yml` | the package is already in the job's workspace, or the credentials come from a secret manager in the same job |

## Package layout

```
<path>/
  manifest.json            required; a JSON object
  docs/<version>/...       one directory per kind present
  xref/<version>/...
  schemas/<version>/...
```

- The root holds `manifest.json` and kind directories only. The allowed kinds come from the input `kinds` (default `docs,xref,schemas`). At least one kind must be present; absent kinds are not written, but their prefixes are still checked (see Immutability). A storage contract that fixes the kinds (for example `docs`, `xref` and `schemas` only) is kept by the caller: pass a subset of those in `kinds`, or keep the default.
- Each kind directory holds exactly one directory, named after the version, and that entry must be a real directory (not a link, not a file). One run publishes one version.
- Regular files only: a symbolic link, a device or a name with a control character or a backslash fails the run. Each file is at most 100 MiB.
- `manifest.json`: when it has a `package` field, it must equal `package-name`. When it has a `latest` field, it must equal `version`; with an empty `version` input, `latest` is the version. When it has a `versions` field (an object keyed by version, or a list), it must name the version. Any other field is the caller's: the component neither reads nor changes it.

## Object keys

| Key | Written | Rule |
| --- | --- | --- |
| `<package-name>/<kind>/<version>/<path>` | once | immutable: never replaced, never deleted |
| `<package-name>/manifest.json` | every run, last, unless `write-manifest: false` | mutable pointer: replaced by each publication |

Object `Content-Type` comes from the file extension (`.md` is `text/markdown; charset=utf-8`; unknown extensions are `application/octet-stream`).

## What it overwrites and deletes

- **Deletes nothing.** Earlier versions stay in the bucket.
- **Overwrites only** `<package-name>/manifest.json`, and only with `write-manifest: true` (the default). The component writes the caller's file as is: when a reader needs every published version listed there, the caller's manifest must list them. Publishing an older version line (a patch of a previous minor) replaces the pointer with that manifest as well. When the storage contract of the bucket has no per-package manifest, set `write-manifest: false`: the run then writes the immutable version objects only.

## Immutability

- **Before any write**, the run lists the `<package-name>/<kind>/<version>/` prefix of every kind in `kinds`, including the kinds absent from the package. Any object there fails the run: a published version cannot gain a kind later; ship a new version. A listing that fails (credentials, endpoint, network) also fails the run: the check fails closed.
- **Each write** is conditional (`If-None-Match: *`). If another run wrote the same key in the meantime, the store answers 412 and the run fails. A retry after a lost answer accepts the object only when its ETag equals the MD5 of the local file, that is, when it holds exactly the bytes this run sent.
- **No force option.** The component never replaces a version object.
- **Partial failure.** When a write fails after the retries, the run starts no further write and does not write the manifest, but the objects already written (and the writes already in flight) stay. A new run of the same version then fails at the prefix check. Remove the partial prefix by hand (with the bucket owner's credentials) or publish a new version.

## Values: input, repository variable, organization variable

Each value comes from the most specific level that sets it:

1. an explicit input in the caller's `with:`;
2. a variable of the calling repository;
3. a variable of the calling organization.

| Input | Variable | Without input and variable |
| --- | --- | --- |
| `bucket` | `CLOUDFLARE_R2_DOCS_PUBLISH_BUCKET` | the run fails before any write |

The endpoint is a secret, never an input or a variable, because it holds the account id (`https://<account-id>.r2.cloudflarestorage.com`) and variables render unmasked in the run logs, which a public repository shows to anyone. Store the three secrets as `CLOUDFLARE_R2_DOCS_PUBLISH_ENDPOINT`, `CLOUDFLARE_R2_DOCS_PUBLISH_ACCESS_KEY_ID` and `CLOUDFLARE_R2_DOCS_PUBLISH_SECRET_ACCESS_KEY` and pass each with one explicit line (never `secrets: inherit`). Use an R2 API token scoped to the target bucket with object read and write permission (the prefix check lists objects).

The composite action reads no variable: it receives every value through `with:`.

## Inputs

"Both" means the reusable workflow and the composite action.

| Input | Form | Required | Default | Rule |
| --- | --- | --- | --- | --- |
| `artifact-name` | reusable | yes | | workflow artifact of the same run that holds the package |
| `path` | both | composite: yes | reusable: `.` | reusable: directory inside the artifact; composite: directory relative to the workspace. Never absolute, never `..`, never a link out of the workspace |
| `package-name` | both | no | calling repository name | `^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$` |
| `version` | both | no | `latest` of `manifest.json` | `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`, no `..` |
| `kinds` | both | no | `docs,xref,schemas` | kind names `^[a-z0-9][a-z0-9-]{0,62}$`, separated by commas or new lines; keep it to the kinds of the bucket's storage contract |
| `bucket` | both | yes | variable (reusable) | `^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$` |
| `endpoint` | composite | unless dry run | empty | `https://<host>[:port]`, no path; for R2 `https://<account-id>.r2.cloudflarestorage.com`; pass from a secret: GitHub masks a secret in the logs, while a variable or a literal is printed in the step header |
| `write-manifest` | both | no | `true` | write `<package-name>/manifest.json` after the version objects |
| `dry-run` | both | no | `false` | validate and list only |
| `timeout-minutes` | reusable | no | `15` | job timeout |
| `access-key-id` | composite | unless dry run | empty | pass from a secret |
| `secret-access-key` | composite | unless dry run | empty | pass from a secret |

## Secrets (reusable workflow)

| Secret | Required | Content |
| --- | --- | --- |
| `endpoint` | unless `dry-run` | S3 API endpoint, same rule as the composite input `endpoint` |
| `access-key-id` | unless `dry-run` | S3 access key id |
| `secret-access-key` | unless `dry-run` | S3 secret access key |

## Outputs

| Output | Content |
| --- | --- |
| `version` | the version published (planned, in a dry run) |
| `objects` | number of version objects, `manifest.json` excluded |
| `payload-sha256` | SHA-256 of the listing `<sha256>  <key>` (two spaces, one line per object, `manifest.json` included when written, sorted by key). The run log prints the listing; `sha256sum` of a saved copy gives the same value |

## Permissions

The job needs `contents: read` only: the artifact download and the R2 writes do not use the GitHub token.

## Concurrency

The reusable workflow runs one publication at a time per package (`cloudflare-r2-docs-publish-<package-name>-publish`) and never cancels a running one. GitHub keeps only one pending job per group: when a third publication starts while one runs and one waits, the waiting one is cancelled. Run it again; its version is not yet in the bucket. A dry run has its own group per run.

## Calling the reusable workflow

Reference the component by its major tag `cloudflare-r2-docs-publish-v1`. A fork and a caller outside the maintainer organization pin the full commit SHA of a release tag instead, with the tag as a comment (`@<sha40> # cloudflare-r2-docs-publish-vX.Y.Z`). Find the SHA with `gh api repos/<owner>/actions/git/ref/tags/cloudflare-r2-docs-publish-vX.Y.Z` (dereference an annotated tag).

```yaml
jobs:
  build-docs:
    runs-on: ubuntu-24.04
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@<sha40> # vX.Y.Z
        with:
          persist-credentials: false
      - run: npm ci && npm run docs:build   # writes .docs-dist/ (manifest.json, docs/<version>/, ...)
      - uses: actions/upload-artifact@<sha40> # vX.Y.Z
        with:
          name: docs-package
          path: .docs-dist
          if-no-files-found: error

  publish-docs:
    needs: build-docs
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/cloudflare-r2-docs-publish.yml@cloudflare-r2-docs-publish-v1
    with:
      artifact-name: docs-package
    secrets:
      endpoint: ${{ secrets.CLOUDFLARE_R2_DOCS_PUBLISH_ENDPOINT }}
      access-key-id: ${{ secrets.CLOUDFLARE_R2_DOCS_PUBLISH_ACCESS_KEY_ID }}
      secret-access-key: ${{ secrets.CLOUDFLARE_R2_DOCS_PUBLISH_SECRET_ACCESS_KEY }}
```

`actions/upload-artifact` skips hidden files unless `include-hidden-files: true`; a hidden entry at the package root fails the run anyway.

## Calling the composite action

Use the composite action when the credentials come from a secret manager inside the job: the reusable workflow accepts secrets only from the caller's `secrets` context. The component never talks to the secret manager.

```yaml
jobs:
  publish-docs:
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    permissions:
      contents: read
    concurrency:
      group: docs-publish-${{ github.repository }}
      cancel-in-progress: false
    steps:
      - uses: actions/checkout@<sha40> # vX.Y.Z
        with:
          persist-credentials: false
      - run: npm ci && npm run docs:build
      - id: credentials
        # Any step that outputs the two values; its own token stays in this step.
        uses: <secret-manager-action>@<sha40> # vX.Y.Z
      - uses: <owner>/actions/cloudflare-r2-docs-publish@cloudflare-r2-docs-publish-v1
        with:
          path: .docs-dist
          bucket: ${{ vars.CLOUDFLARE_R2_DOCS_PUBLISH_BUCKET }}
          endpoint: ${{ secrets.CLOUDFLARE_R2_DOCS_PUBLISH_ENDPOINT }}
          access-key-id: ${{ steps.credentials.outputs.access-key-id }}
          secret-access-key: ${{ steps.credentials.outputs.secret-access-key }}
```

## Implementation notes

- The upload is a standard-library Python client (`scripts/s3.py`): path-style requests signed with AWS Signature Version 4, region `auto`. The AWS CLI of the runner image was not used: one process per conditional `put-object` is slow for a generated reference of thousands of files, `aws s3 sync`/`cp` cannot make a conditional write, and recent AWS CLI versions need extra checksum settings for R2. The tests check the signature against the AWS documentation examples and run the client against a local fake store.
- Up to 8 objects are written in parallel; the first failed write stops every write not yet started. Network errors, 429 and 5xx answers are retried up to 3 attempts per request.
- Redirects are never followed: a 3xx answer fails the request, so the signed headers never reach another host or scheme.
- Error messages name the operation, the key, the HTTP status and the S3 error code; they never print a response body or a credential.
