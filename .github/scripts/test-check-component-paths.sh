#!/usr/bin/env bash
# Regression tests for check-component-paths.sh. Each case builds a small
# git repository with one example component, commits a change and requires
# the expected exit status.
#
# Usage: test-check-component-paths.sh [repository-root]
set -euo pipefail

ROOT="$(cd "${1:-.}" && pwd)"
readonly ROOT
readonly CHECK="$ROOT/.github/scripts/check-component-paths.sh"
readonly COMPONENT="github-example-check"
readonly WORKFLOW=".github/workflows/${COMPONENT}.yml"
WORK="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/check-component-paths.XXXXXX")"
readonly WORK
trap 'rm -rf "$WORK"' EXIT
failures=0
cases=0

# Creates a repository with the example component, its workflow, an
# internal workflow and a root README, and prints its path.
new_repo() {
  local repo
  repo="$(mktemp -d "$WORK/repo.XXXXXX")"
  (
    cd "$repo"
    git init -q
    git config user.name test
    git config user.email test@example.invalid
    mkdir -p "$COMPONENT" .github/workflows .github/scripts
    printf 'name: Example\n' >"$COMPONENT/action.yml"
    printf '# Example\n' >"$COMPONENT/README.md"
    printf 'name: Example\n' >"$WORKFLOW"
    printf 'name: CI\n' >.github/workflows/_ci.yml
    printf '# Repository\n' >README.md
    git add -A
    git commit -q -m base
  )
  printf '%s\n' "$repo"
}

# Runs one case: $1 name, $2 expected status (0 or 1), $3... files to
# change in a commit on top of the base.
run_case() {
  local name="$1" expected="$2" repo actual file
  shift 2
  cases=$((cases + 1))
  repo="$(new_repo)"
  for file in "$@"; do
    mkdir -p "$repo/$(dirname "$file")"
    printf 'changed\n' >>"$repo/$file"
  done
  git -C "$repo" add -A
  git -C "$repo" commit -q -m change
  actual=0
  (cd "$repo" && bash "$CHECK" HEAD~1 HEAD >/dev/null) || actual=$?
  if [[ "$actual" -ne "$expected" ]]; then
    printf 'FAIL: %s: expected exit %s, got %s\n' "$name" "$expected" "$actual" >&2
    failures=$((failures + 1))
  else
    printf 'ok: %s\n' "$name"
  fi
}

run_case "workflow only fails" 1 "$WORKFLOW"
run_case "workflow with the component folder passes" 0 "$WORKFLOW" "$COMPONENT/README.md"
run_case "component folder only passes" 0 "$COMPONENT/action.yml"
run_case "internal workflow only passes" 0 ".github/workflows/_ci.yml"
run_case "root files and scripts pass" 0 README.md .github/scripts/example.sh
run_case "workflow of a non-component passes" 0 ".github/workflows/other.yml"
run_case "workflow with another folder fails" 1 "$WORKFLOW" "other/README.md"

# A usage error exits 2.
cases=$((cases + 1))
actual=0
bash "$CHECK" >/dev/null 2>&1 || actual=$?
if [[ "$actual" -ne 2 ]]; then
  printf 'FAIL: usage error: expected exit 2, got %s\n' "$actual" >&2
  failures=$((failures + 1))
else
  printf 'ok: usage error\n'
fi

printf '%s cases, %s failures\n' "$cases" "$failures"
[[ "$failures" -eq 0 ]]
