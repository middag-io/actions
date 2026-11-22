# go-module-scan

Scans the packages of a Go module for known vulnerabilities with [govulncheck](https://go.dev/doc/tutorial/govulncheck) and fails only on a blocking severity. Publishes nothing.

A composite action, `go-module-scan/action.yml`: it scans in a job of the caller, with the Go of that job (for example `actions/setup-go` with `go-version-file: go.mod` before this action).

## What it does

1. Validates the inputs before anything else: `working-directory` stays inside the checkout and holds a `go.mod`; `blocking-severities` lists one or more of `critical`, `high`, `medium`, `low`.
2. Prints the scan command and the govulncheck version. A dry run stops here.
3. Installs `golang.org/x/vuln/cmd/govulncheck@v1.9.0` with `go install` into a temporary directory, removed after the scan. `go install` checks the module against the Go checksum database. The install runs with `GOTOOLCHAIN=auto`, so a Go older than the one govulncheck needs downloads that toolchain for the install only.
4. Runs `govulncheck -format json ./...` in `working-directory`, with the Go of the job (source mode, symbol level). The standard library findings are those of that Go version: set up the version of the `toolchain` line of `go.mod` (`actions/setup-go` with `go-version-file: go.mod` reads it, else the `go` line).
5. Prints every vulnerability once, at the deepest level found, as an annotation: an error when its severity is in `blocking-severities`, a warning otherwise. The annotation names the Go vulnerability ID, its aliases (CVE, GHSA), the module and version, the called symbol, the fixed version and the link.
6. Fails when at least one vulnerability has a blocking severity, when the install fails, or when govulncheck exits with an error or prints no JSON report (for example, the vulnerability database or the module proxy is unreachable, or the packages do not build).

## Severity mapping

The Go vulnerability database (`vuln.go.dev`) gives no CVSS severity: its OSV records have no `severity` field. govulncheck reports how far the code reaches a vulnerability instead. The component maps that reachability to a severity:

| govulncheck finding | Severity |
| --- | --- |
| symbol level: the code calls a vulnerable function or method | `critical` |
| package level: the code imports a vulnerable package but calls no vulnerable symbol | `low` |
| module level: the module requires a vulnerable module but imports no vulnerable package | `low` |

- The default, `critical`, fails exactly when govulncheck itself fails in its text mode: on a called vulnerable symbol.
- `low` also fails on vulnerable code that is imported or required but not called.
- `high` and `medium` match no finding; they are accepted so that one value serves every scan component of this repository.

The mapping says nothing about the impact of a vulnerability. Read the advisory behind the link of each annotation.

## What it deletes

Nothing in the repository. The component writes the govulncheck binary to a temporary directory and removes it; `go install` and govulncheck fill the Go module and build caches of the job.

## Inputs

| Input | Default | Meaning |
| --- | --- | --- |
| `working-directory` | `.` | Directory that holds `go.mod`, relative to the checkout. |
| `blocking-severities` | `critical` | Severities that fail the scan, separated by commas (see "Severity mapping"). |
| `dry-run` | `false` | Validate and print the command only. |

## Outputs

| Output | Meaning |
| --- | --- |
| `command` | The scan command, run or planned. |
| `tool` | The Go package and version of govulncheck that the scan installs. |
| `blocking-severities` | The blocking severities, comma-separated, from the highest. |
| `advisories` | Number of vulnerabilities found (empty in a dry run). |
| `blocking` | Number of vulnerabilities with a blocking severity (empty in a dry run). |

## Variables and secrets

The action reads no variable and no secret. A caller that keeps the severities in a variable passes them in `with:`, for example `blocking-severities: ${{ vars.GO_MODULE_SCAN_BLOCKING_SEVERITIES || 'critical' }}`. A module with private dependencies needs `GOPRIVATE` and the credentials of those dependencies set up in the job before the step.

## Permissions

The caller grants `contents: read` to the calling job. The component asks for nothing more.

## Example

Reference the component by its major tag `go-module-scan-v1`. A fork and a caller outside the maintainer organization pin the full commit SHA of a release tag instead, with the tag as a comment (`@<sha40> # go-module-scan-vX.Y.Z`). Find the SHA with `gh api repos/<owner>/actions/git/ref/tags/go-module-scan-vX.Y.Z` (dereference an annotated tag).

```yaml
steps:
  - uses: actions/checkout@<sha40> # vX.Y.Z
    with:
      persist-credentials: false
  - uses: actions/setup-go@<sha40> # vX.Y.Z
    with:
      go-version-file: go.mod
  - uses: <owner>/actions/go-module-scan@go-module-scan-v1
    with:
      blocking-severities: critical, low
```
