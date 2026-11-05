# github-release-major-check

Checks that a pull request asks for a new MAJOR version only explicitly. release-please cuts a MAJOR from a `!` in a Conventional Commits header or from a `BREAKING CHANGE:` footer; with this check, a breaking change marker passes only when the pull request also carries a `Release-As: X.0.0` footer, with `X` above the MAJOR of the package version in the release-please manifest of the base commit. Publishes nothing.

Two forms, one name:

- reusable workflow `.github/workflows/github-release-major-check.yml`: checks out the head of the pull request with its history and runs the check;
- composite action `github-release-major-check/action.yml`: runs the check in a job of the caller, on a checkout the caller made (with the history of the base and head commits).

## What it does

1. Validates the inputs before anything else: `manifest-file` and `package` (relative paths), the title, and outside a dry run the full SHAs of the base and head commits.
2. Reads the version of `package` from the release-please manifest **at the base commit** (`git show <base>:<manifest-file>`).
3. Reads the pull request title and every commit `base..head` that is not a merge commit (`git log`). A pull request kept current by merging the base branch into it is checked on its own commits only: the merge commits and the commits they bring from the base branch are skipped.
4. Reads every `Release-As:` footer of those commits (any case) and fails when one:
   - is not a SemVer version (a leading `v` is accepted);
   - raises the MAJOR to anything but `X.0.0` (for example `3.1.0` or `3.0.0-rc.1` on `2.4.1`);
   - is not above the base version (for example `2.4.1` or `1.9.0` on `2.4.1`).

   A `Release-As:` of a MINOR or a PATCH above the base version passes. Two different `X.0.0` footers in one pull request fail.
5. Reads the Conventional Commits header of the title and of each commit (`!` before `:`) and its `BREAKING CHANGE:` or `BREAKING-CHANGE:` footer. When no commit carries a valid `Release-As: X.0.0` above the base MAJOR, each breaking change marker is a finding. The match is lenient, so that it recognises at least what the release tooling reads as a breaking change: the type in any case, white space around the scope, `!` and `:` (`feat (api) ! : x`), and white space before the colon of a footer (`BREAKING CHANGE : x`).
6. Every other message passes, a message that is not a Conventional Commit included (`Revert "..."`, for example): this component gates the MAJOR, not the commit format.

The rule applies at every version, `0.x` included: a breaking change on `0.9.3` needs `Release-As: 1.0.0`.

The title carries no footer: release-please reads `Release-As:` from the commits that reach the base branch. Merge the pull request with a merge commit, so that its commits, and the footer, reach the base branch as they are.

Each finding is an error annotation that names the title or the commit, followed by what to do: add a commit with the footer `Release-As: X.0.0` (an empty commit is enough) when the change is meant to start a new MAJOR, or keep the change compatible otherwise. A commit with the wrong type is not rewritten: its change goes to a new pull request with the right type.

In a dry run (`dry-run: true`) the manifest is read from the workspace, only the title is checked, no git history is read, and findings are warnings: the run never fails on them.

## What it deletes

Nothing. The component writes no file in the repository and publishes nothing.

## Inputs

| Input | Workflow | Action | Default | Meaning |
| --- | --- | --- | --- | --- |
| `manifest-file` | yes | yes | `.release-please-manifest.json` | release-please manifest, read at the base commit. |
| `package` | yes | yes | `.` | Key of the package in the manifest: `.` for a single package, the package folder in a monorepo. |
| `title` | yes | yes (required) | the title of the pull request of the event (workflow) | Title to check. |
| `base-sha` | no | yes | empty | Full SHA of the base commit; required unless `dry-run`. The workflow passes the one of the event. |
| `head-sha` | no | yes | empty | Full SHA of the head commit; required unless `dry-run`. The workflow passes the one of the event. |
| `dry-run` | yes (boolean) | yes (`true`/`false`) | `false` | Check the title against the manifest of the workspace; report only. |
| `timeout-minutes` | yes | no | `5` | Timeout of the job. |

A monorepo with several packages calls the component once per package, each with its own `package`. The calling jobs run in parallel (see "Concurrency").

## Outputs

| Output | Meaning |
| --- | --- |
| `version` | Version of the package in the manifest of the base commit. |
| `release-as-major` | The new MAJOR named by a `Release-As:` footer of the pull request (`X.0.0`), or empty. |
| `result` | `pass` or `fail`. |
| `findings` | Number of findings. |

## Variables and secrets

None.

## Permissions

The caller grants `contents: read` to the calling job. The component asks for nothing more: the title and the SHAs come from the event, the commits and the manifest from the checkout.

## Concurrency

The job of the reusable workflow has one concurrency group per run attempt and package: `github-release-major-check-<run id>-<run attempt>-<package>`, with `root` for the package `.`. The component writes nothing outside the job, so the group only keeps two calls for the same package in one run from running together; it never cancels a job in progress.

A monorepo caller that runs one job per package in the same run gets one group per job: every job runs in parallel, and none waits for or cancels another. Give each call a distinct `package`.

## Triggers

The calling workflow runs on `pull_request` with the types `opened`, `edited` (a new title), `synchronize` (new commits) and `reopened`. Make the check required in the ruleset of the base branch, so that a pull request cannot merge while it fails. The status check context is `<caller job id> / check`: with the example below, `github-release-major-check / check`. The ruleset entry must match it exactly.

The release pull request of release-please passes: its title and its one commit carry no breaking change marker and no footer.

## Example

Reference the component by the full commit SHA of a release tag of this component, with the tag as a comment. Find the SHA with `gh api repos/<owner>/actions/git/ref/tags/github-release-major-check-vX.Y.Z` (dereference an annotated tag).

```yaml
name: Release major

on:
  pull_request:
    types: [opened, edited, synchronize, reopened]

permissions: {}

jobs:
  github-release-major-check:
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/github-release-major-check.yml@<sha40> # github-release-major-check-vX.Y.Z
```

The composite action in a job of the caller:

```yaml
steps:
  - uses: actions/checkout@<sha40> # vX.Y.Z
    with:
      ref: ${{ github.event.pull_request.head.sha }}
      fetch-depth: 0
      persist-credentials: false
  - uses: <owner>/actions/github-release-major-check@<sha40> # github-release-major-check-vX.Y.Z
    with:
      title: ${{ github.event.pull_request.title }}
      base-sha: ${{ github.event.pull_request.base.sha }}
      head-sha: ${{ github.event.pull_request.head.sha }}
```
