#!/usr/bin/env bash
# Checks that a change to the reusable workflow of a component also changes
# its folder (README.md, "Versioning and releases").
#
# release-please assigns a commit to a component only through the files
# under the component folder (`<component>/`). `.github/workflows/<component>.yml`
# is outside that folder, so a pull request that changes only the workflow
# releases nothing. With merge commits, the merge commit carries the pull
# request title and every file of the pull request, so one file under
# `<component>/` in the same pull request is enough.
#
# Usage: check-component-paths.sh <base-ref> [<head-ref>]
#
# Compares <base-ref> with <head-ref> (default HEAD). A component is a
# top-level directory with an action.yml or action.yaml at <head-ref>.
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  printf 'usage: %s <base-ref> [<head-ref>]\n' "${0##*/}" >&2
  exit 2
fi

readonly BASE="$1"
readonly HEAD="${2:-HEAD}"
readonly WORKFLOW_DIR=".github/workflows"
status=0

changed="$(git diff --name-only --no-renames "$BASE" "$HEAD" --)"
readonly changed

# True when <component> holds an action at the head ref.
is_component() {
  git cat-file -e "$HEAD:$1/action.yml" 2>/dev/null ||
    git cat-file -e "$HEAD:$1/action.yaml" 2>/dev/null
}

# True when a changed file sits under <component>/.
folder_changed() {
  local file
  while IFS= read -r file; do
    [[ "$file" == "$1/"* ]] && return 0
  done <<<"$changed"
  return 1
}

while IFS= read -r file; do
  [[ "$file" == "$WORKFLOW_DIR"/* ]] || continue
  name="${file#"$WORKFLOW_DIR"/}"
  [[ "$name" == */* || "$name" == _* ]] && continue
  case "$name" in
    *.yml) name="${name%.yml}" ;;
    *.yaml) name="${name%.yaml}" ;;
    *) continue ;;
  esac
  is_component "$name" || continue
  if ! folder_changed "$name"; then
    printf '::error file=%s::%s changes without a change under %s/; release-please releases a component only from its folder, so change a file there in the same pull request (for example the README of the component)\n' \
      "$file" "$file" "$name"
    status=1
  fi
done <<<"$changed"

exit "$status"
