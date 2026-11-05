#!/usr/bin/env bash
# Regression tests for ci-changes.sh and ci-gate.sh. Each ci-changes.sh case
# builds a small git repository with two example components, commits a
# change and requires the expected plan. Each ci-gate.sh case feeds job
# results and a plan and requires the expected exit status.
#
# Usage: test-ci-changes.sh [repository-root]
set -euo pipefail

ROOT="$(cd "${1:-.}" && pwd)"
readonly ROOT
readonly CHANGES="$ROOT/.github/scripts/ci-changes.sh"
readonly GATE="$ROOT/.github/scripts/ci-gate.sh"
WORK="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/ci-changes.XXXXXX")"
readonly WORK
trap 'rm -rf "$WORK"' EXIT
failures=0
cases=0

# Creates a repository with the components alpha-check and beta-check, their
# workflows, an internal workflow, a script and a root README, and prints
# its path.
new_repo() {
  local repo
  repo="$(mktemp -d "$WORK/repo.XXXXXX")"
  (
    cd "$repo"
    git init -q
    git config user.name test
    git config user.email test@example.invalid
    mkdir -p alpha-check beta-check .github/workflows .github/scripts docs
    printf 'name: Alpha\n' >alpha-check/action.yml
    printf 'name: Beta\n' >beta-check/action.yaml
    printf '# Alpha\n' >alpha-check/README.md
    printf '# Beta\n' >beta-check/README.md
    printf 'name: Alpha\n' >.github/workflows/alpha-check.yml
    printf 'name: Beta\n' >.github/workflows/beta-check.yml
    printf 'name: CI\n' >.github/workflows/_ci.yml
    printf 'echo\n' >.github/scripts/tool.sh
    printf '{}\n' >.release-please-manifest.json
    printf '# Repository\n' >README.md
    printf '# Notes\n' >docs/notes.md
    git add -A
    git commit -q -m base
  )
  printf '%s\n' "$repo"
}

# expect_plan <description> <event> <branch> <expected> <file>...
# Appends a line to each <file>, commits, and compares the plan.
expect_plan() {
  local description="$1" event="$2" branch="$3" expected="$4" repo actual file
  shift 4
  cases=$((cases + 1))
  repo="$(new_repo)"
  (
    cd "$repo"
    for file in "$@"; do
      mkdir -p "$(dirname "$file")"
      printf 'change\n' >>"$file"
    done
    git add -A
    git commit -q -m change
  )
  actual="$(cd "$repo" && bash "$CHANGES" "$event" "$branch" | tr '\n' ' ')"
  if [[ "$actual" == "$expected " ]]; then
    printf 'ok   %s\n' "$description"
  else
    printf 'FAIL %s: expected "%s", got "%s"\n' "$description" "$expected" "$actual"
    failures=$((failures + 1))
  fi
}

# expect_gate <description> <expected-status> <needs-json> <components> <full>
expect_gate() {
  local description="$1" expected="$2" status=0
  cases=$((cases + 1))
  NEEDS_JSON="$3" COMPONENTS="$4" FULL="$5" bash "$GATE" >/dev/null 2>&1 || status=$?
  if [[ "$status" == "$expected" ]]; then
    printf 'ok   %s\n' "$description"
  else
    printf 'FAIL %s: expected status %s, got %s\n' "$description" "$expected" "$status"
    failures=$((failures + 1))
  fi
}

expect_plan "push runs no dry-run" push main \
  'components=[] full=false' alpha-check/README.md
expect_plan "component folder change runs its dry-run" pull_request feature \
  'components=["alpha-check"] full=true' alpha-check/README.md
expect_plan "component workflow change runs its dry-run" pull_request feature \
  'components=["beta-check"] full=true' .github/workflows/beta-check.yml
expect_plan "two components run two dry-runs" pull_request feature \
  'components=["alpha-check","beta-check"] full=true' alpha-check/README.md beta-check/README.md
expect_plan "root documentation runs no dry-run" pull_request feature \
  'components=[] full=true' README.md docs/notes.md
expect_plan "CI workflow change runs every dry-run" pull_request feature \
  'components=["alpha-check","beta-check"] full=true' .github/workflows/_ci.yml
expect_plan "script change runs every dry-run" pull_request feature \
  'components=["alpha-check","beta-check"] full=true' .github/scripts/tool.sh
expect_plan "release pull request with changelogs only runs nothing" pull_request release-please--branches--main \
  'components=[] full=false' .release-please-manifest.json alpha-check/CHANGELOG.md
expect_plan "release pull request with another file runs the checks" pull_request release-please--branches--main \
  'components=["alpha-check"] full=true' alpha-check/CHANGELOG.md alpha-check/README.md
expect_plan "changelog on another branch runs the checks" pull_request feature \
  'components=["alpha-check"] full=true' .release-please-manifest.json alpha-check/CHANGELOG.md

all_ok='{"changes":{"result":"success"},"actionlint":{"result":"success"},"unit-tests":{"result":"success"},"dry-run-alpha-check":{"result":"success"},"dry-run-check-alpha-check":{"result":"success"}}'
expect_gate "every job succeeded" 0 "$all_ok" '["alpha-check"]' true
skipped_alpha='{"changes":{"result":"success"},"actionlint":{"result":"success"},"unit-tests":{"result":"success"},"dry-run-alpha-check":{"result":"skipped"},"dry-run-check-alpha-check":{"result":"skipped"},"dry-run-beta-check-monorepo-api":{"result":"success"}}'
expect_gate "unplanned dry-run may be skipped" 0 "$skipped_alpha" '["beta-check"]' true
expect_gate "planned dry-run may not be skipped" 1 "$skipped_alpha" '["alpha-check","beta-check"]' true
release='{"changes":{"result":"success"},"actionlint":{"result":"success"},"check-genericity":{"result":"skipped"},"unit-tests":{"result":"skipped"},"dry-run-alpha-check":{"result":"skipped"}}'
expect_gate "release pull request skips the heavy jobs" 0 "$release" '[]' false
expect_gate "planned unit-tests may not be skipped" 1 "$release" '[]' true
expect_gate "a light job may not be skipped" 1 '{"changes":{"result":"success"},"actionlint":{"result":"skipped"}}' '[]' false
expect_gate "a failed plan fails the gate" 1 '{"changes":{"result":"failure"},"dry-run-alpha-check":{"result":"skipped"}}' '[]' false
expect_gate "a skipped plan fails the gate" 1 '{"changes":{"result":"skipped"}}' '[]' false
expect_gate "a failed dry-run fails the gate" 1 '{"changes":{"result":"success"},"dry-run-alpha-check":{"result":"failure"},"dry-run-check-alpha-check":{"result":"skipped"}}' '["alpha-check"]' true
expect_gate "a cancelled job fails the gate" 1 '{"changes":{"result":"success"},"gitleaks":{"result":"cancelled"}}' '[]' false

printf '%d cases, %d failures\n' "$cases" "$failures"
((failures == 0))
