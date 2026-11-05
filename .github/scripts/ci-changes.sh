#!/usr/bin/env bash
# Plans which CI jobs of _ci.yml run, from the event and the changed files.
#
# Usage: ci-changes.sh <event-name> <head-branch> [<base-ref> [<head-ref>]]
#
# Prints two lines for $GITHUB_OUTPUT:
#   components=<JSON array>  components whose dry-run jobs run
#   full=<true|false>        whether check-genericity and unit-tests run
#
# - A push (to main) runs neither: the pull request already ran them on the
#   same merge, and the release workflow does not depend on this one.
# - The release pull request of release-please (head branch
#   "release-please--*") that changes only CHANGELOG.md files and
#   .release-please-manifest.json runs neither.
# - Any other pull request runs check-genericity and unit-tests, and the
#   dry-run of each component whose folder (<component>/) or reusable
#   workflow (.github/workflows/<component>.yml) changed. A change to any
#   other file under .github/ (this script, _ci.yml, the other scripts, the
#   lint configuration) runs the dry-run of every component.
#
# <base-ref> and <head-ref> default to HEAD^1 and HEAD: in a pull request,
# HEAD is the merge commit and its first parent is the base branch. A
# component is a top-level directory with an action.yml or action.yaml.
set -euo pipefail

if [[ $# -lt 2 || $# -gt 4 ]]; then
  printf 'usage: %s <event-name> <head-branch> [<base-ref> [<head-ref>]]\n' "${0##*/}" >&2
  exit 2
fi

readonly EVENT="$1"
readonly BRANCH="$2"
readonly BASE="${3:-HEAD^1}"
readonly HEAD="${4:-HEAD}"
readonly WORKFLOW_DIR=".github/workflows"
readonly RELEASE_BRANCH_PREFIX="release-please--"
readonly RELEASE_FILES_RE='^(\.release-please-manifest\.json|([^/]+/)*CHANGELOG\.md)$'

# Prints the plan and exits.
emit() {
  printf 'components=%s\nfull=%s\n' "$1" "$2"
  exit 0
}

# Prints the components at <head-ref>, one per line, sorted.
list_components() {
  git ls-tree -r --name-only "$HEAD" |
    grep -E '^[^/]+/action\.ya?ml$' | cut -d/ -f1 | sort -u || true
}

# Prints a JSON array of its arguments.
to_json() {
  if [[ $# -eq 0 ]]; then
    printf '[]'
    return
  fi
  printf '%s\n' "$@" | jq -R . | jq -c -s .
}

if [[ "$EVENT" != pull_request ]]; then
  emit '[]' false
fi

changed="$(git diff --name-only --no-renames "$BASE" "$HEAD" --)"
readonly changed

if [[ "$BRANCH" == "$RELEASE_BRANCH_PREFIX"* && -n "$changed" ]] &&
  ! grep -qvE "$RELEASE_FILES_RE" <<<"$changed"; then
  emit '[]' false
fi

mapfile -t components < <(list_components)
declare -A selected=()
all=0
while IFS= read -r file; do
  [[ -n "$file" ]] || continue
  top="${file%%/*}"
  if [[ "$file" == "$WORKFLOW_DIR"/* ]]; then
    name="${file#"$WORKFLOW_DIR"/}"
    name="${name%.yml}"
    name="${name%.yaml}"
    if [[ " ${components[*]} " == *" $name "* ]]; then
      selected["$name"]=1
      continue
    fi
  fi
  if [[ "$top" == .github ]]; then
    all=1
  elif [[ "$file" == */* && " ${components[*]} " == *" $top "* ]]; then
    selected["$top"]=1
  fi
done <<<"$changed"

if ((all)); then
  emit "$(to_json "${components[@]}")" true
fi
mapfile -t chosen < <(printf '%s\n' "${!selected[@]}" | grep . | sort || true)
emit "$(to_json "${chosen[@]+"${chosen[@]}"}")" true
