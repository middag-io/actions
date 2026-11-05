#!/usr/bin/env bash
# Decides the aggregated required check ci-gate of _ci.yml.
#
# Usage: ci-gate.sh
# Environment:
#   NEEDS_JSON  toJSON(needs) of the ci-gate job
#   COMPONENTS  JSON array of the components planned by ci-changes.sh
#   FULL        "true" when check-genericity and unit-tests were planned
#
# Every job must succeed, except a job that the plan of the changes job
# skips: check-genericity and unit-tests when FULL is not "true", and the
# dry-run jobs (dry-run-<component>[-*], dry-run-check-<component>[-*]) of a
# component outside COMPONENTS. The changes job itself must succeed, so a
# failed plan never lets a skipped job pass.
set -euo pipefail

: "${NEEDS_JSON:?NEEDS_JSON is required}"
: "${COMPONENTS:?COMPONENTS is required}"
: "${FULL:?FULL is required}"

# Prints "planned" when the plan runs <job>, "skipped" otherwise.
plan_of() {
  local job="$1" rest component
  case "$job" in
    check-genericity | unit-tests)
      [[ "$FULL" == true ]] && echo planned || echo skipped
      return
      ;;
    dry-run-check-*) rest="${job#dry-run-check-}" ;;
    dry-run-*) rest="${job#dry-run-}" ;;
    *)
      echo planned
      return
      ;;
  esac
  while IFS= read -r component; do
    if [[ "$rest" == "$component" || "$rest" == "$component"-* ]]; then
      echo planned
      return
    fi
  done < <(jq -r '.[]' <<<"$COMPONENTS")
  echo skipped
}

failed=""
while IFS=$'\t' read -r job result; do
  if [[ "$result" == success ]]; then
    printf '%s: success\n' "$job"
  elif [[ "$result" == skipped && "$job" != changes && "$(plan_of "$job")" == skipped ]]; then
    printf '%s: skipped (not planned)\n' "$job"
  else
    failed+="${job}: ${result}"$'\n'
  fi
done < <(jq -r 'to_entries[] | "\(.key)\t\(.value.result)"' <<<"$NEEDS_JSON")

if [[ -n "$failed" ]]; then
  printf '::error::Required jobs did not succeed:\n%s' "$failed"
  exit 1
fi
