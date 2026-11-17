# github-release-date-check

Checks that no commit of a pull request is dated before the last release. release-please walks the history of the base branch from its tip, newest committer date first, and stops at the commit of the last release. A commit whose committer date is earlier than that release commit comes after it in the walk, so release-please never reads it: the change is missing from the changelog and does not count for the next version. A `feat` lost this way releases a PATCH instead of a MINOR. It happens to a branch whose commits were made before the last release and merged after it. Publishes nothing.

Two forms, one name:

- reusable workflow `.github/workflows/github-release-date-check.yml`: checks out the head of the pull request with its history, the branches and the tags, and runs the check;
- composite action `github-release-date-check/action.yml`: runs the check in a job of the caller, on a checkout the caller made (see "Example").

## What it does

1. Validates the inputs before anything else: `manifest-file` and `package` (relative paths), `tag-format` (exactly one `{version}`), and outside a dry run the name of the base branch and the full SHA of the head commit.
2. Reads the version of `package` from the release-please manifest **at the tip of the base branch when the check runs** (`git show origin/<base-ref>:<manifest-file>`), not at the base commit of the event: a re-run sees a release that landed after the pull request was opened.
3. Finds the commit of the release tag of that version (`tag-format` with the version in place of `{version}`) and reads its committer date. A missing tag is a warning and the check passes (`result` is `skip`): there is no release yet, or the release workflow has not created the tag yet.
4. Reads every commit `origin/<base-ref>..head` that is not a merge commit, and fails on each one whose committer date is not later than the committer date of the release commit. A pull request kept current by merging the base branch into it is checked on its own commits only.

Only the committer date counts. `git cherry-pick` and `git commit --amend` give a commit a new committer date and keep its author date.

Each finding is an error annotation that names the commit and its committer date, followed by what to do: recreate the branch from the current base branch with cherry-pick and open a new pull request from it, without rebasing or force-pushing the published branch:

```bash
git fetch origin
git switch -c <new-branch> origin/main
git rev-list --reverse --no-merges origin/main..<head of the old pull request> | git cherry-pick --stdin
git push -u origin <new-branch>
```

Then close the old pull request.

In a dry run (`dry-run: true`) the manifest is read from the workspace and the release tag is only reported: no git history is read and `result` is `skip`.

## What it deletes

Nothing. The component writes no file in the repository and publishes nothing.

## Inputs

| Input | Workflow | Action | Default | Meaning |
| --- | --- | --- | --- | --- |
| `manifest-file` | yes | yes | `.release-please-manifest.json` | release-please manifest, read at the tip of the base branch. |
| `package` | yes | yes | `.` | Key of the package in the manifest: `.` for a single package, the package folder in a monorepo. |
| `tag-format` | yes | yes | `v{version}` | Release tag of a version: `v{version}` for the release-please default, `{version}` with `include-v-in-tag: false`, `<component>-v{version}` for a component in a monorepo. |
| `base-ref` | no | yes | empty | Name of the base branch, read as `refs/remotes/origin/<base-ref>`; required unless `dry-run`. The workflow passes the one of the event. |
| `head-sha` | no | yes | empty | Full SHA of the head commit; required unless `dry-run`. The workflow passes the one of the event. |
| `dry-run` | yes (boolean) | yes (`true`/`false`) | `false` | Read the manifest of the workspace and report the release tag only. |
| `timeout-minutes` | yes | no | `5` | Timeout of the job. |
| `runner` | yes | no | `ubuntu-24.04` | Runner label for the jobs, for example the label of a self-hosted runner; the default keeps the GitHub-hosted runner. |

A monorepo with several packages calls the component once per package, each with its own `package` and `tag-format`.

## Outputs

| Output | Meaning |
| --- | --- |
| `version` | Version of the package in the manifest of the base branch. |
| `tag` | Release tag of that version. |
| `release-sha` | Commit of the release tag; empty when the tag is missing or in a dry run. |
| `result` | `pass`, `fail`, or `skip` (no release tag, or a dry run). |
| `findings` | Number of findings. |

## Variables and secrets

None.

## Permissions

The caller grants `contents: read` to the calling job. The component asks for nothing more: the base branch and the head SHA come from the event, the commits, the manifest and the tags from the checkout.

## Concurrency

The job of the reusable workflow has one concurrency group per run attempt and package: `github-release-date-check-<run id>-<run attempt>-<package>`, with `root` for the package `.`. The component writes nothing outside the job, so the group never cancels a job in progress.

## Triggers

The calling workflow runs on `pull_request` with the types `opened`, `synchronize` and `reopened`. A release that lands on the base branch after the check ran does not run it again: run the check again (re-run the job) before merging a pull request that was opened before the last release. To block the merge, make the check required in the ruleset of the base branch; the status check context is `<caller job id> / check`.

The release pull request of release-please passes: its one commit is made after the last release.

## Example

Reference the component by its major tag `github-release-date-check-v1`. A fork and a caller outside the maintainer organization pin the full commit SHA of a release tag instead, with the tag as a comment (`@<sha40> # github-release-date-check-vX.Y.Z`). Find the SHA with `gh api repos/<owner>/actions/git/ref/tags/github-release-date-check-vX.Y.Z` (dereference an annotated tag).

```yaml
name: Release date

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions: {}

jobs:
  github-release-date-check:
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/github-release-date-check.yml@github-release-date-check-v1
```

The composite action in a job of the caller. `fetch-depth: 0` makes `actions/checkout` fetch every branch as `refs/remotes/origin/*` and every tag:

```yaml
steps:
  - uses: actions/checkout@<sha40> # vX.Y.Z
    with:
      ref: ${{ github.event.pull_request.head.sha }}
      fetch-depth: 0
      persist-credentials: false
  - uses: <owner>/actions/github-release-date-check@github-release-date-check-v1
    with:
      base-ref: ${{ github.event.pull_request.base.ref }}
      head-sha: ${{ github.event.pull_request.head.sha }}
```
