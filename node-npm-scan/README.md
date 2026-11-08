# node-npm-scan

Audits the npm dependencies of a project for known vulnerabilities, from its `package-lock.json`, and fails only on a blocking severity. Publishes nothing.

Two forms, one name:

- reusable workflow `.github/workflows/node-npm-scan.yml`: checks out the calling repository, sets up Node.js and audits;
- composite action `node-npm-scan/action.yml`: audits in a job of the caller, with the npm of that job.

The `*-check` components of this repository with an npm project run the composite action, with their input `audit-blocking-severities`.

## What it does

1. Validates the inputs before anything else: `working-directory` stays inside the checkout and holds a `package-lock.json`; `blocking-severities` lists one or more of `critical`, `high`, `medium`, `low`.
2. Prints the audit command. A dry run stops here.
3. Runs `npm audit --json --package-lock-only`: the audit reads the lock file only, so no install is needed.
4. Prints every advisory once (from the `via` entries of the report) as an annotation: an error when its severity is in `blocking-severities`, a warning otherwise. npm calls the medium severity `moderate`; the component maps it to `medium`. `info` never blocks.
5. Fails when at least one advisory has a blocking severity, or when npm prints no JSON report or an error (for example, the registry is unreachable).

## What it deletes

Nothing. The component writes no file.

## Inputs

| Input | Workflow | Action | Default | Meaning |
| --- | --- | --- | --- | --- |
| `working-directory` | yes | yes | `.` | Directory that holds `package-lock.json`, relative to the checkout. |
| `blocking-severities` | yes | yes | variable `NODE_NPM_SCAN_BLOCKING_SEVERITIES`, else `critical` (workflow); `critical` (action) | Severities that fail the audit, separated by commas. |
| `node-version` | yes | no | `22` | Node.js version to set up. |
| `dry-run` | yes (boolean) | yes (`true`/`false`) | `false` | Validate and print the command only. |
| `timeout-minutes` | yes | no | `10` | Timeout of the job. |

## Outputs

| Output | Meaning |
| --- | --- |
| `command` | The audit command, run or planned. |
| `blocking-severities` | The blocking severities, comma-separated, from the highest. |
| `advisories` | Number of advisories found (empty in a dry run). |
| `blocking` | Number of advisories with a blocking severity (empty in a dry run). |

## Variables and secrets

| Name | Kind | Read by |
| --- | --- | --- |
| `NODE_NPM_SCAN_BLOCKING_SEVERITIES` | variable | default of `blocking-severities` in the reusable workflow |

No secret.

## Permissions

The caller grants `contents: read` to the calling job. The component asks for nothing more.

## Example

Reference the component by its major tag `node-npm-scan-v1`. A fork and a caller outside the maintainer organization pin the full commit SHA of a release tag instead, with the tag as a comment (`@<sha40> # node-npm-scan-vX.Y.Z`). Find the SHA with `gh api repos/<owner>/actions/git/ref/tags/node-npm-scan-vX.Y.Z` (dereference an annotated tag).

```yaml
jobs:
  node-npm-scan:
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/node-npm-scan.yml@node-npm-scan-v1
    with:
      blocking-severities: critical, high
```

```yaml
steps:
  - uses: <owner>/actions/node-npm-scan@node-npm-scan-v1
    with:
      working-directory: ui
```
