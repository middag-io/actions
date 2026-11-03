# release-please

Runs [release-please](https://github.com/googleapis/release-please) in manifest mode with a GitHub App installation token. The name is the exception for wrappers of a well-known tool (README of this repository, "Naming convention").

Forms:

- reusable workflow: `.github/workflows/release-please.yml` (checks out the configuration and the manifest, then calls the composite action);
- composite action: `release-please/action.yml` (the caller checks out the two files first).

## What it does

1. Checks the inputs, the release-please configuration (`release-please-config.json` by default) and the manifest (`.release-please-manifest.json` by default) of the calling repository: both files exist and hold JSON objects, the configuration has at least one package, every manifest entry is a configured package with a SemVer version, and the paths and the target branch are valid. Outside a dry run, it also requires the GitHub App client id and private key. Any problem fails the run before a token is created.
2. Creates a GitHub App installation token limited to the calling repository, with write access to contents and pull requests ([`actions/create-github-app-token`](https://github.com/actions/create-github-app-token)).
3. Runs [`googleapis/release-please-action`](https://github.com/googleapis/release-please-action) with that token, the configuration and the manifest: it opens or updates the release pull request, and when that pull request merges, it creates the tag and the GitHub release.

A pull request opened with the App token triggers the workflows of the calling repository. A pull request opened with `GITHUB_TOKEN` would not, so this component has no `GITHUB_TOKEN` fallback.

In a dry run (`dry-run: true`) only step 1 runs: no token, no call to the GitHub API, no write.

## What it deletes

It deletes no branch, tag, release or file. What it overwrites, removes or closes, through release-please:

- the release branch `release-please--branches--<target branch>`: release-please **force-pushes** it on every run that updates the release pull request, which discards any commit pushed to it by hand;
- the title and body of the open release pull request, rewritten on every update;
- the label `autorelease: pending` of a merged release pull request, removed and replaced by `autorelease: tagged` once its releases exist;
- issues: the release pull request body lists every commit since the last release. release-please-action v5.0.0 (release-please 17.6.0) writes "closes #N" there for each issue reference in a commit message, `Refs #N` included, so merging the release pull request closes those issues. The change to "refs" is in release-please 17.10.4 and later, which no release-please-action release ships yet ([googleapis/release-please pull request 2851](https://github.com/googleapis/release-please/pull/2851)). Until then, keep issue references (`#N`) out of commit messages and out of pull request titles in a repository that uses this component.

What it creates:

- the release branch and the release pull request, with its labels;
- the tag and the GitHub release of each package released by the run, and a comment on the merged release pull request that lists them;
- the `CHANGELOG.md` and version files of each package, through the release pull request.

## Inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `config-file` | no | `release-please-config.json` | Path of the release-please configuration, relative to the repository root. |
| `manifest-file` | no | `.release-please-manifest.json` | Path of the release-please manifest, relative to the repository root. |
| `target-branch` | no | `""` | Branch that the release pull request targets. Empty uses the default branch. |
| `app-client-id` | unless `dry-run` | variable `RELEASE_PLEASE_APP_CLIENT_ID` (reusable workflow only) | Client id of the GitHub App. |
| `dry-run` | no | `false` | Check the inputs, the configuration and the manifest only. |
| `timeout-minutes` | no | `10` | Timeout of the job, in minutes (reusable workflow only). |

The composite action takes the private key as the input `app-private-key`; the reusable workflow takes it as a secret.

## Outputs

| Output | Description |
| --- | --- |
| `release-created` | `true` when this run created a release of the root package (`.`), `false` otherwise (always `false` in a dry run). |
| `tag-name` | Tag of the release of the root package created by this run; empty when none. |
| `version` | Version of the release of the root package created by this run; empty when none. |
| `packages` | Package paths of the configuration, as a sorted JSON array (for example `["."]`). |

The release outputs describe the root package only. A configuration without a root package gets `release-created: false` from every run.

## Variables and secrets

| Name | Kind | Fills |
| --- | --- | --- |
| `RELEASE_PLEASE_APP_CLIENT_ID` | variable (repository or organization) | input `app-client-id` |
| `RELEASE_PLEASE_APP_PRIVATE_KEY` | secret, passed with one explicit line | secret `app-private-key` |

The secret is never inherited: the caller passes it in its `secrets:` block.

## Permissions

- Calling job: `contents: read`. It only checks out the configuration and the manifest; every write uses the App token.
- GitHub App installed on the calling repository, with read and write access to **Contents** and **Pull requests**. The token asks for these two permissions only. The [release-please-action README](https://github.com/googleapis/release-please-action#workflow-permissions) also lists `issues: write` for its token; without it, the release labels (`autorelease: pending`, `autorelease: tagged`) must already exist in the repository or be creatable with the pull request permission.

## Triggers

The calling workflow runs on `push` to the release branch and, to retry or to apply a `Release-As:` commit, on `workflow_dispatch`. A `workflow_run` trigger (release after the CI of the release branch succeeds) also works. Outside a dry run, the configuration and the manifest are checked on the target branch (the `target-branch` input, else the default branch), the branch release-please reads, whatever branch started the run. Do not add `paths` or `paths-ignore` to that trigger: the merge of the release pull request must always run release-please, or the tag and the release are never created.

## Example

```yaml
name: Release

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions: {}

jobs:
  release-please:
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/release-please.yml@<sha40> # release-please-vX.Y.Z
    secrets:
      app-private-key: ${{ secrets.RELEASE_PLEASE_APP_PRIVATE_KEY }}

  publish:
    needs: release-please
    if: ${{ needs.release-please.outputs.release-created == 'true' }}
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    permissions:
      contents: read
    steps:
      - name: Show the release
        env:
          TAG_NAME: ${{ needs.release-please.outputs.tag-name }}
        run: echo "released $TAG_NAME"
```

As a step, after a checkout of the two files:

```yaml
- name: Check out the release-please configuration
  uses: actions/checkout@<sha40> # vX.Y.Z
  with:
    persist-credentials: false
- name: Run release-please
  id: release
  uses: <owner>/actions/release-please@<sha40> # release-please-vX.Y.Z
  with:
    app-client-id: ${{ vars.RELEASE_PLEASE_APP_CLIENT_ID }}
    app-private-key: ${{ secrets.RELEASE_PLEASE_APP_PRIVATE_KEY }}
```

Find the SHA of a release tag as described in the README of this repository, "Referencing a component".
