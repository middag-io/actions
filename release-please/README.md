# release-please

Runs [release-please](https://github.com/googleapis/release-please) in manifest mode with a GitHub App installation token. The name is the exception for wrappers of a well-known tool (README of this repository, "Naming convention").

Forms:

- reusable workflow: `.github/workflows/release-please.yml` (checks out the configuration and the manifest, then calls the composite action);
- composite action: `release-please/action.yml` (the caller checks out the two files first).

## What it does

1. Checks the inputs, the release-please configuration (`release-please-config.json` by default) and the manifest (`.release-please-manifest.json` by default) of the calling repository: both files exist and hold JSON objects, the configuration has at least one package, every manifest entry is a configured package with a SemVer version, and the paths and the target branch are valid. Outside a dry run, it also requires the GitHub App client id and private key. Any problem fails the run before a token is created.
2. Creates a GitHub App installation token limited to the calling repository, with write access to contents and pull requests ([`actions/create-github-app-token`](https://github.com/actions/create-github-app-token)).
3. Runs [`googleapis/release-please-action`](https://github.com/googleapis/release-please-action) with that token, the configuration and the manifest: it opens or updates the release pull request, and when that pull request merges, it creates the tag and the GitHub release.

4. Unless `dedup-merge-commits` is `false`, and only when step 3 created or updated a release pull request (the `prs_created` output of release-please-action), drops the merge-commit duplicates from the release notes. See "Merge-commit duplicates" below.

A pull request opened with the App token triggers the workflows of the calling repository. A pull request opened with `GITHUB_TOKEN` would not, so this component has no `GITHUB_TOKEN` fallback.

In a dry run (`dry-run: true`) only step 1 runs: no token, no call to the GitHub API, no write.

## Merge-commit duplicates

release-please reads the body of a GitHub default merge commit (`Merge pull request #N from x`, a blank line, then `fix(scope): subject`) as a commit of its own, with the sha of the merge commit. The `CHANGELOG.md` and the release pull request body then list the change twice: the merge commit and the original commit, with the same subject. A merge made with an empty commit message does not produce the duplicate.

With `dedup-merge-commits: true` (the default), after release-please opens or updates the release pull request, `scripts/dedup_release_notes.py` removes an entry when both hold:

1. its sha is a merge commit (two or more parents), and
2. another entry of the same version section has the same subject (the line without its trailing `([sha](url))`) and is not a merge commit.

The entry of the original commit stays. Two ordinary commits with the same subject both stay, and so does a merge-commit entry whose subject is unique. A group heading (`### Bug Fixes`) left empty is removed.

What the step does, for each release pull request of the run:

- clones the release branch with its full commit history (no file contents) into a temporary directory, leaving the workspace of the caller alone, and deduplicates the top version section of the `CHANGELOG.md` of each configured package that the release changes;
- if a `CHANGELOG.md` changed, commits it as `chore: drop merge-commit duplicates` and pushes it to the release branch with a normal push (never forced; the push is rejected if the branch moved meanwhile);
- deduplicates the pull request body and writes it back (`PATCH /pulls/{number}`) only if it changed.

The GitHub release notes come from the **body of the merged release pull request**, not from the `CHANGELOG.md` section: release-please parses the body of the merged pull request and uses the section of each package as the notes of its release. The body edit is therefore what cleans the GitHub release; the `CHANGELOG.md` commit cleans the file. The step does both.

Failure handling: the script first deduplicates the files in the temporary clone, then runs `dedup_release_notes.py --check` on all of them. If a duplicate remains, or a commit of the notes is not found in the clone, the step fails with an error annotation, and nothing was pushed or edited. `--check` is also the way to audit a changelog by hand: `python3 -I release-please/scripts/dedup_release_notes.py --check --changelog CHANGELOG.md` prints the duplicates and exits 1 when there is one. The script is idempotent: a second run changes nothing.

Things to know:

- release-please force-pushes the release branch and rewrites the body whenever the notes it computes differ from the body that is there, and once this step has edited the body it differs from what release-please computes. Each run that updates the pull request therefore discards the cleanup commit and applies it again. The commit on the release branch is the last of the run, so the merged branch keeps it.
- The push to the release branch is made with the App token, so workflows that listen to `push` on that branch or to `pull_request` run for it. Do not trigger the release workflow itself on `push` to `release-please--*` branches: it would run release-please again, which would rewrite the branch, and this step would push again, in a loop. The recommended trigger (`push` to the default branch, and `workflow_dispatch`) does not loop.
- The commit is not signed. If a ruleset requires signed commits on the release branch, the push is rejected and the step fails; set `dedup-merge-commits: false` there.
- The step needs `git`, `gh`, `jq` and `python3` on the runner (all present on the GitHub-hosted `ubuntu-24.04` image).
- A release pull request body that release-please moved to a file because it is too long for GitHub is not handled.

## What it deletes

It deletes no branch, tag, release or file. What it overwrites, removes or closes, through release-please:

- the release branch `release-please--branches--<target branch>`: release-please **force-pushes** it on every run that updates the release pull request, which discards any commit pushed to it by hand;
- the title and body of the open release pull request, rewritten on every update (and then edited again by the merge-commit duplicates step);
- the commit `chore: drop merge-commit duplicates` that the same step pushes to the release branch, discarded by the next force-push and applied again;
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
| `dedup-merge-commits` | no | `true` | Drop merge-commit duplicates from the release notes (the `CHANGELOG.md` files and the body of the release pull request). `false` keeps the notes as release-please writes them. |
| `app-client-id` | unless `dry-run` | variable `RELEASE_PLEASE_APP_CLIENT_ID` (reusable workflow only) | Client id of the GitHub App. |
| `dry-run` | no | `false` | Check the inputs, the configuration and the manifest only. |
| `timeout-minutes` | no | `10` | Timeout of the job, in minutes (reusable workflow only). |
| `runner` | no | `ubuntu-24.04` | Runner label for the jobs, for example the label of a self-hosted runner; the default keeps the GitHub-hosted runner. |

The composite action takes the private key as the input `app-private-key`; the reusable workflow takes it as a secret.

## Outputs

| Output | Description |
| --- | --- |
| `release-created` | `true` when this run created a release of the root package (`.`), `false` otherwise (always `false` in a dry run). |
| `tag-name` | Tag of the release of the root package created by this run; empty when none. |
| `version` | Version of the release of the root package created by this run; empty when none. |
| `packages` | Package paths of the configuration, as a sorted JSON array (for example `["."]`). |
| `paths-released` | Paths of the packages released by this run, as a JSON array; `[]` when none. Use it to publish a package of a monorepo. |

The `release-created`, `tag-name` and `version` outputs describe the root package only. A configuration without a root package gets `release-created: false` from every run.

## Variables and secrets

| Name | Kind | Fills |
| --- | --- | --- |
| `RELEASE_PLEASE_APP_CLIENT_ID` | variable (repository or organization) | input `app-client-id` |
| `RELEASE_PLEASE_APP_PRIVATE_KEY` | secret, passed with one explicit line | secret `app-private-key` |

The secret is never inherited: the caller passes it in its `secrets:` block.

## Permissions

- Calling job: `contents: read`. It only checks out the configuration and the manifest; every write uses the App token (including the push and the body edit of the merge-commit duplicates step).
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
    uses: <owner>/actions/.github/workflows/release-please.yml@release-please-v1
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
  uses: <owner>/actions/release-please@release-please-v1
  with:
    app-client-id: ${{ vars.RELEASE_PLEASE_APP_CLIENT_ID }}
    app-private-key: ${{ secrets.RELEASE_PLEASE_APP_PRIVATE_KEY }}
```

Reference the component by its major tag `release-please-v1`. A fork and a caller outside the maintainer organization pin the full commit SHA of a release tag instead, with the tag as a comment (`@<sha40> # release-please-vX.Y.Z`). Find the SHA of a release tag as described in the README of this repository, "Referencing a component".
