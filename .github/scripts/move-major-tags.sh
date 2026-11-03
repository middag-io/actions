#!/usr/bin/env bash
# Moves the major tag <component>-vN of each component released in this run
# to the commit of its release (README.md, "Versioning and releases").
# Called by _release.yml after release-please, with the token of the release
# App. A pre-release moves nothing.
#
# Environment:
#   RELEASE_OUTPUTS    JSON of the outputs of release-please-action
#                      (paths_released, <path>--tag_name, <path>--sha, ...)
#   GITHUB_REPOSITORY  owner/repository
#   GH_TOKEN           token allowed to write tags (read by gh)
#
# Usage: move-major-tags.sh
set -euo pipefail

: "${RELEASE_OUTPUTS:?RELEASE_OUTPUTS is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"

readonly RELEASE_TAG_RE='^([a-z0-9]+(-[a-z0-9]+)*)-v([0-9]+)\.([0-9]+)\.([0-9]+)(-[0-9A-Za-z.-]+)?$'
readonly SHA_RE='^[0-9a-f]{40}$'

# Prints the value of output <key>, or nothing when it is absent.
output() {
  jq -r --arg key "$1" '.[$key] // empty' <<<"$RELEASE_OUTPUTS"
}

# Creates refs/tags/$1 at commit $2, or moves it there when it exists.
move_tag() {
  local tag="$1" sha="$2"
  if gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$tag" >/dev/null 2>&1; then
    gh api --method PATCH "repos/$GITHUB_REPOSITORY/git/refs/tags/$tag" \
      -f sha="$sha" -F force=true >/dev/null
    printf 'moved %s to %s\n' "$tag" "$sha"
  else
    gh api --method POST "repos/$GITHUB_REPOSITORY/git/refs" \
      -f ref="refs/tags/$tag" -f sha="$sha" >/dev/null
    printf 'created %s at %s\n' "$tag" "$sha"
  fi
}

paths="$(output paths_released)"
mapfile -t released < <(jq -r '.[]' <<<"${paths:-[]}")

for path in "${released[@]}"; do
  tag_name="$(output "$path--tag_name")"
  sha="$(output "$path--sha")"
  if [[ ! "$tag_name" =~ $RELEASE_TAG_RE ]]; then
    printf '::error::%s: release tag %q is not <component>-vX.Y.Z\n' "$path" "$tag_name"
    exit 1
  fi
  component="${BASH_REMATCH[1]}"
  major="${BASH_REMATCH[3]}"
  prerelease="${BASH_REMATCH[6]}"
  if [[ ! "$sha" =~ $SHA_RE ]]; then
    printf '::error::%s: release commit %q is not a 40-character SHA\n' "$path" "$sha"
    exit 1
  fi
  if [[ -n "$prerelease" ]]; then
    printf 'skipped %s: pre-release\n' "$tag_name"
    continue
  fi
  move_tag "$component-v$major" "$sha"
done
