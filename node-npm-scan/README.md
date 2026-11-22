# node-npm-scan

Audits the npm dependencies of a project for known vulnerabilities, from its `package-lock.json`, and fails only on a blocking severity. Publishes nothing.

A composite action, `node-npm-scan/action.yml`: it audits in a job of the caller, with the npm of that job. The caller checks out its repository and sets up Node.js before the step.

The `*-check` components of this repository with an npm project run the composite action, with their input `audit-blocking-severities`.

## What it does

1. Validates the inputs before anything else: `working-directory` stays inside the checkout and holds a `package-lock.json`; `blocking-severities` lists one or more of `critical`, `high`, `medium`, `low`.
2. Prints the audit command. A dry run stops here.
3. Runs `npm audit --json --package-lock-only`: the audit reads the lock file only, so no install is needed. With `omit-dev` true, it adds `--omit=dev` and audits only the production dependencies. The log and the job summary name the mode used: full (all dependencies) or production only.
4. Prints every advisory once (from the `via` entries of the report) as an annotation: an error when its severity is in `blocking-severities`, a warning otherwise. npm calls the medium severity `moderate`; the component maps it to `medium`. `info` never blocks.
5. Fails when at least one advisory has a blocking severity, or when npm prints no JSON report or an error (for example, the registry is unreachable).

## What it deletes

Nothing. The component writes no file.

## Inputs

| Input | Default | Meaning |
| --- | --- | --- |
| `working-directory` | `.` | Directory that holds `package-lock.json`, relative to the checkout. |
| `blocking-severities` | `critical` | Severities that fail the audit, separated by commas. |
| `omit-dev` | `false` | `true` audits only the production dependencies (`npm audit --omit=dev`). |
| `dry-run` | `false` | `true` validates and prints the command only. |

## Outputs

| Output | Meaning |
| --- | --- |
| `command` | The audit command, run or planned. |
| `blocking-severities` | The blocking severities, comma-separated, from the highest. |
| `advisories` | Number of advisories found (empty in a dry run). |
| `blocking` | Number of advisories with a blocking severity (empty in a dry run). |

## Variables and secrets

The action reads no variable and no secret. A caller that keeps the values in variables passes them in `with:`, for example `blocking-severities: ${{ vars.NODE_NPM_SCAN_BLOCKING_SEVERITIES || 'critical' }}`.

## Permissions

The caller grants `contents: read` to the calling job. The component asks for nothing more.

## Example

Reference the component by its major tag `node-npm-scan-v1`. A fork and a caller outside the maintainer organization pin the full commit SHA of a release tag instead, with the tag as a comment (`@<sha40> # node-npm-scan-vX.Y.Z`). Find the SHA with `gh api repos/<owner>/actions/git/ref/tags/node-npm-scan-vX.Y.Z` (dereference an annotated tag).

```yaml
steps:
  - uses: actions/checkout@<sha40> # vX.Y.Z
    with:
      persist-credentials: false
  - uses: actions/setup-node@<sha40> # vX.Y.Z
    with:
      node-version: 22
  - uses: <owner>/actions/node-npm-scan@node-npm-scan-v1
    with:
      working-directory: ui
      blocking-severities: critical, high
```
