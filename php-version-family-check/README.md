# php-version-family-check

Checks a pull request against the window of a **version family**: a set of packages (for example PHP libraries in separate repositories) that share the MAJOR, where PATCH carries only fixes, a feature waits for a MINOR window and a breaking change waits for a family MAJOR. Until `per-lib-from` the MINOR is shared too (a **family** MINOR, one coordinated version); from `per-lib-from` on, each package bumps its own MINOR (a **per-lib** MINOR) and only the MAJOR stays coordinated. Publishes nothing.

A composite action, `php-version-family-check/action.yml`: it runs the check in a job of the caller, on a checkout the caller made (with the history of the base and head commits).

## The window

One value, usually a variable of the repository (never of the organization), set and cleared by whoever owns the releases:

| Value | Meaning |
| --- | --- |
| empty | closed: no feature, no breaking change |
| `X.Y` with `Y > 0` (for example `2.1`) | MINOR window: features pass. A family MINOR window by default; a per-lib MINOR window of one package from `per-lib-from` (see "Per-lib minor") |
| `X.0` (for example `3.0`) | family MAJOR window: features and breaking changes pass |

The component only reads the window. It never writes a variable, so no token of the calling job needs a permission on variables.

A window counts as open for a pull request only when both hold, and as closed otherwise:

- the pull request goes into the window branch (`window-branch`; the caller passes the default branch of the repository, see "Example"). A pull request into a maintenance branch such as `release/X.Y` always sees the window closed;
- the window is ahead of the package version at the base commit: a MINOR window `X.Y` when `X` is the MAJOR of that version and `Y` is above its MINOR, a MAJOR window `X.0` when `X.0.0` is above that version (so `2.0` is open on `1.x` and `0.x`, the cut of the family). A window left set after its family release (window `2.1` on a package at `2.1.x`) is closed: a feature does not slip into a PATCH;
- for a MINOR window, the package version at the base commit is not a prerelease. A package at `X.Y.Z-rc.N` never has a MINOR window open, with or without `per-lib-from`: the freeze of the candidates is real from `rc.1`. A MAJOR window stays open on a candidate, so that the final version can be named.

The annotation says why a set window counts as closed.

## Release candidates

A family can cut its first version as release candidates, `X.Y.0-rc.1`, `X.Y.0-rc.2`, and promote the last one to `X.Y.0`.

- **Cut.** While the window is open, a `Release-As: X.Y.0-rc.N` footer passes, exactly where `Release-As: X.Y.0` passes: `X.Y.0` must be the first version of the window, and `N` is a number from 1 with no leading zero. Everything else stays rejected: another version (`Release-As: 3.0.0-rc.1` in window `2.0`), `rc.0`, `rc.01`, any other prerelease label (`beta`, `alpha`), build metadata, and any `Release-As` rc while the window is closed.
- **Self-closing.** A package at `X.Y.0-rc.N` in the manifest is still below `X.Y.0`, so the window `X.Y` stays open (ahead) until the final `X.Y.0` is released, then it closes by itself.
- **Freeze.** `family-from` may be a prerelease. With `family-from: 2.0.0-rc.1` and the window cleared, a `feat` and a breaking change fail from the first candidate on (manifest `2.0.0-rc.1` or above), while a `fix` passes and, on a package below `2.0.0-rc.1`, a `feat` still passes. A `fix` then bumps only the candidate counter (`2.0.0-rc.2`).

Cut and promotion, as the footers a pull request carries (the window set to `2.0`, then cleared for the freeze):

```text
chore: cut the first candidate

Release-As: 2.0.0-rc.1
```

```text
chore: promote the candidate

Release-As: 2.0.0
```

The step-by-step procedure for a family cut is in [RUNBOOK-rc-cut.md](RUNBOOK-rc-cut.md).

## Per-lib minor

Opt-in, with `per-lib-from` (for example `2.0.0`). Empty keeps the family behaviour above, so existing callers are unchanged. When the package version at the base commit is at least `per-lib-from` and is not a prerelease, the package is in per-lib mode, and a MINOR window `X.Y` is the window of that package alone:

- it opens only when `Y` is the MINOR of the package version plus one, with the same MAJOR (package at `2.3.x`, window `2.4`). Any other `Y` (window `2.6` on `2.3.x`) leaves the window closed, and the annotation says why;
- `feat` passes, and `window-kind` is `per-lib-minor`;
- a breaking change still fails: it needs a MAJOR window;
- every `Release-As` footer fails, a `X.Y.0` or `X.Y.0-rc.N` of the window included: release-please derives the next minor from the `feat` commits, and a free `Release-As` could jump from `2.3` to `2.6`;
- a MAJOR window `X.0` is unchanged: features and breaking changes pass, and `Release-As: X.0.0` or `X.0.0-rc.N` is allowed, because the MAJOR stays coordinated.

The window closes by itself when the minor is released: with the package at `2.4.0` the window `2.4` is no longer ahead. Packages below `per-lib-from` keep the family behaviour.

## What it does

1. Validates the inputs before anything else: the window, `family-from` (SemVer), `manifest-file` and `package` (relative paths), the title, and outside a dry run the full SHAs of the base and head commits.
2. Reads the version of `package` from the release-please manifest **at the base commit** (`git show <base>:<manifest-file>`).
3. Reads the pull request title and every commit `base..head` that is not a merge commit (`git log`). A pull request kept current by merging the base branch into it is checked on its own commits only: the merge commits and the commits they bring from the base branch are skipped.
4. Reads the Conventional Commits header of each message (type, optional scope, optional `!`), its `BREAKING CHANGE:` or `BREAKING-CHANGE:` footer and its `Release-As:` footer, and fails when:
   - a header has `!` before `:`, or a message has a breaking change footer, unless a family MAJOR window is open;
   - a header has the type `feat` while the window is closed, but only when the version of step 2 is at least `family-from` (empty `family-from`: at every version). Below it, a `feat` passes, so a package still on its pre-family line keeps cutting MINOR versions;
   - a message has a `Release-As:` footer (any case, at any version), unless a family MINOR or MAJOR window is open and the footer names its first version `X.Y.0` or a release candidate of it, `X.Y.0-rc.N` (see "Release candidates"). A family version is one coordinated version, never the choice of one pull request. In a per-lib MINOR window every `Release-As` fails (see "Per-lib minor").

   The match is lenient, so that it recognises at least what the release tooling reads as a feature or a breaking change: the type in any case, white space around the scope, `!` and `:`, no space after `:` (`feat:x`, `Feat (api) :x`), and white space before the colon of a footer (`BREAKING CHANGE : x`).
5. Every other message passes, a message that is not a Conventional Commit included (`Revert "..."`, for example): this component enforces the window, not the commit format.

Each finding is an error annotation that names the title or the commit, followed by what to do: keep the pull request open, keep it current by merging the base branch into it (never rebase or force-push), and re-run the check when the window opens. A commit with the wrong type is not rewritten: its change goes to a new pull request with the right type.

In a dry run (`dry-run: true`) the manifest is read from the workspace, only the title is checked, no git history is read, and findings are warnings: the run never fails on them.

## What it deletes

Nothing. The component writes no file in the repository and publishes nothing.

## Inputs

| Input | Default | Meaning |
| --- | --- | --- |
| `window` | empty | The window: empty, `X.Y` or `X.0`. Surrounding white space is ignored. |
| `family-from` | empty | Package version from which a `feat` needs an open window. May be a prerelease (`2.0.0-rc.1`): the family is frozen from its first candidate. |
| `per-lib-from` | empty | Opt-in per-lib mode: from this package version (SemVer, for example `2.0.0`) a MINOR window is per package, open only for the next minor, with no `Release-As`. Empty keeps the family behaviour. |
| `manifest-file` | `.release-please-manifest.json` | release-please manifest, read at the base commit. |
| `package` | `.` | Key of the package in the manifest. |
| `title` | none (required) | Title to check. |
| `base-sha` | empty | Full SHA of the base commit; required unless `dry-run`. |
| `head-sha` | empty | Full SHA of the head commit; required unless `dry-run`. |
| `base-ref` | empty | Name of the base branch of the pull request. With `window-branch`, the window applies only to a pull request into `window-branch`; empty skips that rule. |
| `window-branch` | empty | Branch whose pull requests the window applies to. |
| `dry-run` | `false` | Check the title against the manifest of the workspace; report only. |

## Outputs

| Output | Meaning |
| --- | --- |
| `version` | Version of the package in the manifest. |
| `window-kind` | `closed`, `minor`, `per-lib-minor` or `major`: the window as it applies to this pull request (`closed` when it is set but not ahead of the version, not the next minor in per-lib mode, a MINOR window on a prerelease, or not for the base branch). |
| `result` | `pass` or `fail`. |
| `findings` | Number of findings. |

## Variables and secrets

The action reads no variable and no secret. The caller passes the window in `with:` (`window: ${{ vars.<NAME> }}`, for example `PHP_VERSION_FAMILY_CHECK_WINDOW`) in every repository of the family, so that one name holds the window. Set the variable per repository: after the MAJOR cut the value differs by package (per-lib mode, the next minor of each one), and a leftover organization value would open a window on a repository that has no variable of its own. An unset variable is empty, and empty means a closed window.

## Permissions

The caller grants `contents: read` to the calling job. The component asks for nothing more: the title and the SHAs come from the event, the commits and the manifest from the checkout.

## Triggers

The calling workflow runs on `pull_request` with the types `opened`, `edited` (a new title), `synchronize` (new commits) and `reopened`. Make the check required in the ruleset of the base branch, so that a pull request cannot merge while it fails. The status check context is the name of the calling job. The ruleset entry must match it exactly.

When the window opens, re-run the failed check, or push a merge of the base branch into the pull request. A re-run reads the title, the base and head SHAs and the base branch of the original event: a title changed since then is read by the run of its `edited` event, and a base branch that moved (for example after a release) is read only after a merge of the base branch into the pull request.

## Example

Reference the component by its major tag `php-version-family-check-v1`. A fork and a caller outside the maintainer organization pin the full commit SHA of a release tag instead, with the tag as a comment (`@<sha40> # php-version-family-check-vX.Y.Z`). Find the SHA with `gh api repos/<owner>/actions/git/ref/tags/php-version-family-check-vX.Y.Z` (dereference an annotated tag).

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
      family-from: 2.0.0-rc.1 # or 2.0.0 once the family is final; see "Release candidates"
      # per-lib-from: 2.0.0 # opt-in, see "Per-lib minor"; set it once the family is final
      title: ${{ github.event.pull_request.title }}
      base-sha: ${{ github.event.pull_request.base.sha }}
      head-sha: ${{ github.event.pull_request.head.sha }}
      base-ref: ${{ github.event.pull_request.base.ref }}
      window-branch: ${{ github.event.repository.default_branch }}
```
