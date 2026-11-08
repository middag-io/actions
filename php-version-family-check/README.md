# php-version-family-check

Checks a pull request against the window of a **version family**: a set of packages (for example PHP libraries in separate repositories) that share MAJOR.MINOR, where PATCH carries only fixes, a feature waits for a family MINOR and a breaking change waits for a family MAJOR. Publishes nothing.

Two forms, one name:

- reusable workflow `.github/workflows/php-version-family-check.yml`: checks out the head of the pull request with its history and runs the check;
- composite action `php-version-family-check/action.yml`: runs the check in a job of the caller, on a checkout the caller made (with the history of the base and head commits).

## The window

One value, usually an organization variable set and cleared by whoever owns the family releases:

| Value | Meaning |
| --- | --- |
| empty | closed: no feature, no breaking change |
| `X.Y` with `Y > 0` (for example `2.1`) | family MINOR window: features pass |
| `X.0` (for example `3.0`) | family MAJOR window: features and breaking changes pass |

The component only reads the window. It never writes a variable, so no token of the workflow needs a permission on variables.

A window counts as open for a pull request only when both hold, and as closed otherwise:

- the pull request goes into the window branch (`window-branch`, by default the default branch of the repository). A pull request into a maintenance branch such as `release/X.Y` always sees the window closed;
- the window is ahead of the package version at the base commit: a MINOR window `X.Y` when `X` is the MAJOR of that version and `Y` is above its MINOR, a MAJOR window `X.0` when `X.0.0` is above that version (so `2.0` is open on `1.x` and `0.x`, the cut of the family). A window left set after its family release (window `2.1` on a package at `2.1.x`) is closed: a feature does not slip into a PATCH.

The annotation says why a set window counts as closed.

## What it does

1. Validates the inputs before anything else: the window, `family-from` (SemVer), `manifest-file` and `package` (relative paths), the title, and outside a dry run the full SHAs of the base and head commits.
2. Reads the version of `package` from the release-please manifest **at the base commit** (`git show <base>:<manifest-file>`).
3. Reads the pull request title and every commit `base..head` that is not a merge commit (`git log`). A pull request kept current by merging the base branch into it is checked on its own commits only: the merge commits and the commits they bring from the base branch are skipped.
4. Reads the Conventional Commits header of each message (type, optional scope, optional `!`), its `BREAKING CHANGE:` or `BREAKING-CHANGE:` footer and its `Release-As:` footer, and fails when:
   - a header has `!` before `:`, or a message has a breaking change footer, unless a family MAJOR window is open;
   - a header has the type `feat` while the window is closed, but only when the version of step 2 is at least `family-from` (empty `family-from`: at every version). Below it, a `feat` passes, so a package still on its pre-family line keeps cutting MINOR versions;
   - a message has a `Release-As:` footer (any case, at any version), unless a window is open and the footer names its first version `X.Y.0`. A family version is one coordinated version, never the choice of one pull request.

   The match is lenient, so that it recognises at least what the release tooling reads as a feature or a breaking change: the type in any case, white space around the scope, `!` and `:`, no space after `:` (`feat:x`, `Feat (api) :x`), and white space before the colon of a footer (`BREAKING CHANGE : x`).
5. Every other message passes, a message that is not a Conventional Commit included (`Revert "..."`, for example): this component enforces the window, not the commit format.

Each finding is an error annotation that names the title or the commit, followed by what to do: keep the pull request open, keep it current by merging the base branch into it (never rebase or force-push), and re-run the check when the window opens. A commit with the wrong type is not rewritten: its change goes to a new pull request with the right type.

In a dry run (`dry-run: true`) the manifest is read from the workspace, only the title is checked, no git history is read, and findings are warnings: the run never fails on them.

## What it deletes

Nothing. The component writes no file in the repository and publishes nothing.

## Inputs

| Input | Workflow | Action | Default | Meaning |
| --- | --- | --- | --- | --- |
| `window` | yes | yes | variable `PHP_VERSION_FAMILY_CHECK_WINDOW` (workflow); empty (action) | The family window: empty, `X.Y` or `X.0`. Surrounding white space is ignored. |
| `family-from` | yes | yes | empty | Package version from which a `feat` needs an open window. |
| `manifest-file` | yes | yes | `.release-please-manifest.json` | release-please manifest, read at the base commit. |
| `package` | yes | yes | `.` | Key of the package in the manifest. |
| `title` | yes | yes (required) | the title of the pull request of the event (workflow) | Title to check. |
| `base-sha` | no | yes | empty | Full SHA of the base commit; required unless `dry-run`. The workflow passes the one of the event. |
| `head-sha` | no | yes | empty | Full SHA of the head commit; required unless `dry-run`. The workflow passes the one of the event. |
| `base-ref` | no | yes | empty | Name of the base branch of the pull request. With `window-branch`, the window applies only to a pull request into `window-branch`; empty skips that rule. The workflow passes the one of the event. |
| `window-branch` | yes | yes | the default branch of the repository (workflow); empty (action) | Branch whose pull requests the window applies to. |
| `dry-run` | yes (boolean) | yes (`true`/`false`) | `false` | Check the title against the manifest of the workspace; report only. |
| `timeout-minutes` | yes | no | `5` | Timeout of the job. |

## Outputs

| Output | Meaning |
| --- | --- |
| `version` | Version of the package in the manifest. |
| `window-kind` | `closed`, `minor` or `major`: the window as it applies to this pull request (`closed` when it is set but not ahead of the version or not for the base branch). |
| `result` | `pass` or `fail`. |
| `findings` | Number of findings. |

## Variables and secrets

| Name | Kind | Read by |
| --- | --- | --- |
| `PHP_VERSION_FAMILY_CHECK_WINDOW` | variable | default of `window` in the reusable workflow |

No secret. The variable is unset unless the caller defines it, and unset means a closed window. A caller whose window lives in a variable of another name (for example one organization variable for the whole family) passes it in `with:` (`window: ${{ vars.<NAME> }}`) in every repository of the family, so that one name holds the window.

## Permissions

The caller grants `contents: read` to the calling job. The component asks for nothing more: the title and the SHAs come from the event, the commits and the manifest from the checkout.

## Triggers

The calling workflow runs on `pull_request` with the types `opened`, `edited` (a new title), `synchronize` (new commits) and `reopened`. Make the check required in the ruleset of the base branch, so that a pull request cannot merge while it fails. The status check context is `<caller job id> / check`: with the example below, `php-version-family-check / check`. The ruleset entry must match it exactly.

When the window opens, re-run the failed check, or push a merge of the base branch into the pull request. A re-run reads the title, the base and head SHAs and the base branch of the original event: a title changed since then is read by the run of its `edited` event, and a base branch that moved (for example after a release) is read only after a merge of the base branch into the pull request.

## Example

Reference the component by its major tag `php-version-family-check-v1`. A fork and a caller outside the maintainer organization pin the full commit SHA of a release tag instead, with the tag as a comment (`@<sha40> # php-version-family-check-vX.Y.Z`). Find the SHA with `gh api repos/<owner>/actions/git/ref/tags/php-version-family-check-vX.Y.Z` (dereference an annotated tag).

```yaml
name: Version family

on:
  pull_request:
    types: [opened, edited, synchronize, reopened]

permissions: {}

jobs:
  php-version-family-check:
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/php-version-family-check.yml@php-version-family-check-v1
    with:
      # Name the window variable explicitly; without this line the window
      # comes from PHP_VERSION_FAMILY_CHECK_WINDOW.
      window: ${{ vars.<NAME> }}
      family-from: 2.0.0
```

The composite action in a job of the caller:

```yaml
steps:
  - uses: actions/checkout@<sha40> # vX.Y.Z
    with:
      ref: ${{ github.event.pull_request.head.sha }}
      fetch-depth: 0
      persist-credentials: false
  - uses: <owner>/actions/php-version-family-check@php-version-family-check-v1
    with:
      window: ${{ vars.PHP_VERSION_FAMILY_CHECK_WINDOW }}
      family-from: 2.0.0
      title: ${{ github.event.pull_request.title }}
      base-sha: ${{ github.event.pull_request.base.sha }}
      head-sha: ${{ github.event.pull_request.head.sha }}
      base-ref: ${{ github.event.pull_request.base.ref }}
      window-branch: ${{ github.event.repository.default_branch }}
```
