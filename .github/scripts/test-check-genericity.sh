#!/usr/bin/env bash
# Regression tests for check-genericity.sh. Each case copies the repository
# to a temporary directory, adds a valid example component, plants one
# violation and requires a non-zero exit with the expected message. The
# clean repository and the valid example must pass.
#
# Usage: test-check-genericity.sh [repository-root]
set -euo pipefail

ROOT="$(cd "${1:-.}" && pwd)"
readonly ROOT
readonly CHECK="$ROOT/.github/scripts/check-genericity.sh"
readonly COMPONENT="github-example-check"
readonly WORKFLOW=".github/workflows/${COMPONENT}.yml"
readonly CI_WORKFLOW=".github/workflows/_ci.yml"
readonly DRY_RUN_JOB="dry-run-${COMPONENT}"
WORK="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/check-genericity.XXXXXX")"
readonly WORK
trap 'rm -rf "$WORK"' EXIT

# Assembled so that this script does not contain them.
ORGANIZATION="$(printf '%s%s' 'Mid' 'Dag')"
APPROVE="$(printf '%s%s' '--app' 'rove')"
readonly ORGANIZATION APPROVE
failures=0
cases=0

# Copies the repository, without .git, to a new directory and prints it.
new_tree() {
  local tree
  tree="$(mktemp -d "$WORK/tree.XXXXXX")"
  (cd "$ROOT" && tar --exclude=./.git -cf - .) | (cd "$tree" && tar -xf -)
  printf '%s\n' "$tree"
}

# Replaces every occurrence of $2 with $3 in file $1; fails if $2 is absent.
replace_in() {
  local file="$1" old="$2" new="$3" content
  content="$(<"$file")"
  if [[ "$content" != *"$old"* ]]; then
    printf 'test setup: text not found in %s: %s\n' "$file" "$old" >&2
    return 1
  fi
  printf '%s\n' "${content//"$old"/"$new"}" >"$file"
}

# Writes a valid example component: composite action, script, README,
# fixture, reusable workflow that passes its inputs to the composite action,
# and a dry-run job in the CI workflow, listed in the needs: of ci-gate.
add_component() {
  mkdir -p "$COMPONENT/scripts" "$COMPONENT/tests/fixtures/basic" .github/workflows
  cat >"$COMPONENT/action.yml" <<'EOF'
name: Example check
description: Checks an example input.
inputs:
  target:
    description: Target to check.
    required: true
  dry-run:
    description: Check only; no credentials and no writes.
    required: false
    default: "false"
runs:
  using: composite
  steps:
    - name: Check the target
      shell: bash
      env:
        TARGET: ${{ inputs.target }}
      run: test -n "$TARGET"
EOF
  printf '#!/usr/bin/env bash\necho ok\n' >"$COMPONENT/scripts/check.sh"
  printf 'example\n' >"$COMPONENT/tests/fixtures/basic/target.txt"
  printf '# %s\n\nExample component. Never use secrets: inherit.\n' "$COMPONENT" >"$COMPONENT/README.md"
  cat >"$WORKFLOW" <<'EOF'
name: Example check

on:
  workflow_call:
    inputs:
      target:
        description: Target to check.
        type: string
        default: ${{ vars.GITHUB_EXAMPLE_CHECK_TARGET }}
      dry-run:
        description: Check only; no credentials and no writes.
        type: boolean
        default: false
    secrets:
      api-token:
        description: Token for the example API.
        required: false

permissions: {}

jobs:
  check:
    runs-on: ubuntu-24.04
    timeout-minutes: 5
    permissions:
      contents: read
    concurrency:
      group: github-example-check-${{ github.ref }}
    steps:
    - name: Check the target
      if: ${{ inputs.target != '' }}
      env:
        API_TOKEN: ${{ secrets.api-token }}
        TARGET: ${{ inputs.target }}
      run: test -n "$TARGET"
    - name: Run the example check
      uses: $/github-example-check
      with:
        target: ${{ inputs.target }}
        dry-run: ${{ inputs.dry-run }}
EOF
  replace_in "$CI_WORKFLOW" $'\n  ci-gate:\n' "
  ${DRY_RUN_JOB}:
    name: ${DRY_RUN_JOB}
    permissions:
      contents: read
    uses: ./${WORKFLOW}
    with:
      target: example
      dry-run: true

  ci-gate:
"
  replace_in "$CI_WORKFLOW" $'    needs:\n      - actionlint\n' $'    needs:\n      - actionlint\n      - '"${DRY_RUN_JOB}"$'\n'
}

# Runs one case: $1 name, $2 expected exit ("pass" or "fail"), $3 expected
# message (empty for "pass"), then the setup command run inside the tree.
run_case() {
  local name="$1" expected="$2" message="$3" tree output rc=0
  shift 3
  cases=$((cases + 1))
  tree="$(new_tree)"
  (cd "$tree" && "$@")
  output="$(bash "$CHECK" "$tree" 2>&1)" || rc=$?
  if [[ "$expected" == pass && "$rc" -eq 0 ]] \
    || [[ "$expected" == fail && "$rc" -ne 0 && "$output" == *"$message"* ]]; then
    printf 'ok   %s\n' "$name"
    return 0
  fi
  failures=$((failures + 1))
  printf 'FAIL %s (exit %s, expected %s with "%s")\n%s\n' "$name" "$rc" "$expected" "$message" "$output"
}

nothing() { :; }

org_in_content() {
  add_component
  replace_in "$COMPONENT/action.yml" "Checks an example input." "Uses the ${ORGANIZATION} bucket."
}

org_in_directory_name() {
  add_component
  local dir
  dir="$(printf '%s' "$ORGANIZATION" | tr '[:upper:]' '[:lower:]')-docs-sync"
  mkdir -p "$dir"
  cp "$COMPONENT/action.yml" "$dir/action.yml"
}

org_in_other_file() {
  add_component
  mkdir -p "$COMPONENT/lib"
  printf 'BUCKET="%s-docs"\n' "$ORGANIZATION" >"$COMPONENT/lib/cfg.sh"
}

# The changelog that release-please writes at the root of a component
# names the repository and its owner in its links.
generated_changelog() {
  add_component
  printf '# Changelog\n\n## [1.0.1](https://github.com/%s-io/actions/compare/%s-v1.0.0...%s-v1.0.1)\n' \
    "$ORGANIZATION" "$COMPONENT" "$COMPONENT" >"$COMPONENT/CHANGELOG.md"
}

# Only the changelog at the root of a component is left out.
nested_changelog() {
  add_component
  printf '# Changelog\n\nUses the %s bucket.\n' "$ORGANIZATION" >"$COMPONENT/scripts/CHANGELOG.md"
}

self_reference() {
  add_component
  printf 'uses: someowner/actions/x@abc\n' >>"$COMPONENT/README.md"
}

# A URL whose host precedes /actions/ names no owner of this repository.
github_actions_url() {
  add_component
  printf 'See https://github.com/actions/checkout and actions/checkout@v7.\n' >>"$COMPONENT/README.md"
}

private_reference() {
  add_component
  printf 'uses: someowner/actions-private/.github/workflows/x.yml@abc\n' >>"$COMPONENT/README.md"
}

private_profile_reference() {
  add_component
  printf 'uses: someowner/.github-private/.github/workflows/x.yml@abc\n' >>"$COMPONENT/README.md"
}

other_variable() {
  add_component
  replace_in "$WORKFLOW" "vars.GITHUB_EXAMPLE_CHECK_TARGET" "vars.OTHER_VAR"
}

bracket_access() {
  add_component
  replace_in "$WORKFLOW" "vars.GITHUB_EXAMPLE_CHECK_TARGET" "vars['GCP_BUCKET_ORG']"
}

spaced_bracket_access() {
  add_component
  replace_in "$WORKFLOW" "vars.GITHUB_EXAMPLE_CHECK_TARGET" "vars [ 'OTHER_VAR' ]"
}

bulk_access() {
  add_component
  replace_in "$WORKFLOW" "        TARGET: \${{ inputs.target }}" \
    "        ALL: \${{ toJSON(vars) }}
        TARGET: \${{ inputs.target }}"
}

latest_runner() {
  add_component
  replace_in "$WORKFLOW" "runs-on: ubuntu-24.04" "runs-on: ubuntu-latest"
}

inherited_secrets() {
  add_component
  cat >>"$WORKFLOW" <<'EOF'
  nested:
    uses: ./.github/workflows/github-example-check.yml
    permissions:
      contents: read
    secrets: inherit
EOF
}

automatic_approval() {
  add_component
  printf 'gh pr review 1 %s\n' "$APPROVE" >>"$COMPONENT/scripts/check.sh"
}

bad_directory_name() {
  add_component
  mkdir -p gcp-thing-sync
  cp "$COMPONENT/action.yml" gcp-thing-sync/action.yml
}

# The scope go is in the closed list: a component that touches the Go
# toolchain or Go modules.
go_scope_directory_name() {
  add_component
  mkdir -p go-example-scan
  cp "$COMPONENT/action.yml" go-example-scan/action.yml
}

bad_workflow_name() {
  add_component
  cp "$WORKFLOW" .github/workflows/foo-bar-sync.yml
}

four_space_indentation() {
  add_component
  awk '{ match($0, /^ */); printf "%s%s\n", substr($0, 1, RLENGTH) substr($0, 1, RLENGTH), substr($0, RLENGTH + 1) }' \
    "$WORKFLOW" >"$WORKFLOW.tmp"
  mv "$WORKFLOW.tmp" "$WORKFLOW"
}

quoted_on_key() {
  add_component
  replace_in "$WORKFLOW" $'\non:\n' $'\n\'on\':\n'
  replace_in "$WORKFLOW" $'        description: Token for the example API.\n        required: false' \
    $'        description: Token for the example API.'
}

contents_write() {
  add_component
  replace_in "$WORKFLOW" "contents: read" "contents: write"
}

secret_in_workflow_env() {
  add_component
  replace_in "$WORKFLOW" $'permissions: {}\n' $'permissions: {}\n\nenv:\n  WF_CREDS: ${{ secrets.api-token }}\n'
}

secret_in_job_env() {
  add_component
  replace_in "$WORKFLOW" "    concurrency:" $'    env:\n      CREDS: ${{ secrets.api-token }}\n    concurrency:'
}

forbidden_trigger() {
  add_component
  replace_in "$WORKFLOW" $'on:\n  workflow_call:' $'on:\n  issue_comment:\n  workflow_call:'
}

push_to_any_branch() {
  add_component
  replace_in "$WORKFLOW" $'on:\n  workflow_call:' $'on:\n  push:\n    branches: [\'**\']\n  workflow_call:'
}

inline_push_trigger() {
  add_component
  replace_in "$WORKFLOW" $'on:\n  workflow_call:' $'on: [push]\n\nx-unused:\n  workflow_call:'
}

wiring_unknown_input() {
  add_component
  replace_in "$WORKFLOW" "        target: \${{ inputs.target }}" "        tagret: \${{ inputs.target }}"
}

wiring_input_not_passed() {
  add_component
  replace_in "$WORKFLOW" $'    secrets:\n' $'      level:\n        description: Example level.\n        type: string\n        default: "1"\n    secrets:\n'
}

# php-versions is exempt for moodle-plugin-check only.
wiring_php_versions_not_passed() {
  add_component
  replace_in "$WORKFLOW" $'    secrets:\n' $'      php-versions:\n        description: Example versions.\n        type: string\n        default: ""\n    secrets:\n'
}

no_dry_run_input() {
  add_component
  replace_in "$WORKFLOW" $'      dry-run:\n        description: Check only; no credentials and no writes.\n        type: boolean\n        default: false\n' ""
  replace_in "$WORKFLOW" $'\n        dry-run: ${{ inputs.dry-run }}' ""
}

string_dry_run_input() {
  add_component
  replace_in "$WORKFLOW" $'        type: boolean\n        default: false' $'        type: string\n        default: "false"'
}

no_dry_run_fixture() {
  add_component
  rm -rf "$COMPONENT/tests"
}

no_dry_run_job() {
  add_component
  replace_in "$CI_WORKFLOW" "    uses: ./${WORKFLOW}" "    uses: ./.github/workflows/other.yml"
}

dry_run_job_without_dry_run() {
  add_component
  replace_in "$CI_WORKFLOW" $'      target: example\n      dry-run: true\n' $'      target: example\n      dry-run: false\n'
}

dry_run_job_outside_gate() {
  add_component
  replace_in "$CI_WORKFLOW" $'      - actionlint\n      - '"${DRY_RUN_JOB}"$'\n' $'      - actionlint\n'
}

run_case "clean repository passes" pass "" nothing
run_case "valid example component passes" pass "" add_component
run_case "organization name in component content" fail "organization name in a component file;" org_in_content
run_case "organization name in component directory name" fail "organization name in a component file or directory name" org_in_directory_name
run_case "organization name in any file of a component" fail "organization name in a component file;" org_in_other_file
run_case "changelog written by release-please passes" pass "" generated_changelog
run_case "organization name in a nested changelog" fail "organization name in a component file;" nested_changelog
run_case "owner/actions reference" fail "owner/actions path" self_reference
run_case "URL of a GitHub-owned action passes" pass "" github_actions_url
run_case "reference to owner/actions-private" fail "refers to a private repository" private_reference
run_case "reference to owner/.github-private" fail "refers to a private repository" private_profile_reference
run_case "variable outside the component prefix" fail "outside the component convention" other_variable
run_case "bracket access to vars" fail "dynamic or bulk access" bracket_access
run_case "spaced bracket access to vars" fail "dynamic or bulk access" spaced_bracket_access
run_case "toJSON(vars)" fail "dynamic or bulk access" bulk_access
run_case "runs-on other than ubuntu-24.04" fail "runs-on must be ubuntu-24.04" latest_runner
run_case "secrets: inherit" fail "secrets: inherit is forbidden" inherited_secrets
run_case "automatic approval in a component script" fail "automatic pull request approval" automatic_approval
run_case "component directory outside the naming convention" fail "does not follow {scope}-{object}-{verb}" bad_directory_name
run_case "component directory with the go scope" pass "" go_scope_directory_name
run_case "component workflow outside the naming convention" fail "does not follow {scope}-{object}-{verb}" bad_workflow_name
run_case "four-space indentation" fail "unsupported indentation; use two spaces" four_space_indentation
run_case "quoted on key is still read" fail "does not state required" quoted_on_key
run_case "component grants contents: write" fail "grant at most contents: read" contents_write
run_case "secret in workflow env" fail "secret outside a step" secret_in_workflow_env
run_case "secret in job env" fail "secret outside a step" secret_in_job_env
run_case "issue_comment trigger" fail "trigger issue_comment is not allowed" forbidden_trigger
run_case "push to every branch" fail "push runs only on branches: [main]" push_to_any_branch
run_case "inline push trigger" fail "push runs only on branches: [main]" inline_push_trigger
run_case "with: key that is not an action input" fail 'with: key tagret of $/github-example-check is not an input of' wiring_unknown_input
run_case "workflow input not passed to the action" fail 'input level of the reusable workflow is not passed to $/github-example-check' wiring_input_not_passed
run_case "php-versions not passed in another component" fail 'input php-versions of the reusable workflow is not passed to $/github-example-check' wiring_php_versions_not_passed
run_case "component without a dry-run input" fail "component github-example-check declares no dry-run input" no_dry_run_input
run_case "dry-run input that is not boolean" fail "dry-run input of github-example-check must be type: boolean" string_dry_run_input
run_case "component without a dry-run fixture" fail "component github-example-check has no dry-run fixture" no_dry_run_fixture
run_case "component without a dry-run job" fail "component github-example-check has no dry-run job" no_dry_run_job
run_case "CI job that does not set dry-run: true" fail "component github-example-check has no dry-run job" dry_run_job_without_dry_run
run_case "dry-run job outside the needs of ci-gate" fail "dry-run job dry-run-github-example-check of github-example-check is not in the needs: of ci-gate" dry_run_job_outside_gate

printf '%d cases, %d failed\n' "$cases" "$failures"
((failures == 0))
