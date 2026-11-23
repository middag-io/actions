#!/usr/bin/env bash
# Drops the merge-commit duplicates from each release pull request that
# release-please created or updated in this run: in the CHANGELOG.md files
# of the release branch (one commit, pushed normally, never forced) and in
# the pull request body (which is what the GitHub release notes come from).
#
# Environment (all required):
#   PRS_JSON           the "prs" output of release-please-action (JSON array)
#   CONFIG_FILE        release-please configuration, relative to the repository root
#   GH_TOKEN           GitHub App installation token (contents + pull requests write)
#   GITHUB_REPOSITORY  owner/name
#   GITHUB_SERVER_URL  for example https://github.com
#   RUNNER_TEMP        scratch directory
#
# Nothing is pushed or edited unless every file was deduplicated and then
# passed the --check mode of dedup_release_notes.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly DEDUP="$SCRIPT_DIR/dedup_release_notes.py"
readonly COMMIT_MESSAGE="chore: drop merge-commit duplicates"
readonly BRANCH_RE='^[A-Za-z0-9._/-]+$'

fail() {
  echo "::error::$*"
  exit 1
}

for name in PRS_JSON CONFIG_FILE GH_TOKEN GITHUB_REPOSITORY GITHUB_SERVER_URL RUNNER_TEMP; do
  [ -n "${!name:-}" ] || fail "dedup-merge-commits: environment variable $name is empty"
done
for tool in git gh jq python3; do
  command -v "$tool" >/dev/null || fail "dedup-merge-commits: $tool is not installed on the runner (or set dedup-merge-commits: false)"
done

# The token reaches git through the environment, never through argv.
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0="http.${GITHUB_SERVER_URL}/.extraheader"
GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 | tr -d '\n')"
export GIT_CONFIG_VALUE_0
export GH_REPO="$GITHUB_REPOSITORY"

dedup_pull_request() {
  local number="$1" head="$2" base="$3" work
  [[ "$number" =~ ^[0-9]+$ ]] || fail "dedup-merge-commits: invalid pull request number '$number'"
  [[ "$head" =~ $BRANCH_RE && "$base" =~ $BRANCH_RE ]] || fail "dedup-merge-commits: invalid branch name '$head' or '$base'"
  work="$(mktemp -d "$RUNNER_TEMP/dedup-release-pr.XXXXXX")"

  # Full commit history (no file contents): the script needs the parents of
  # every commit in the notes. The release branch is built on the base branch.
  git init --quiet "$work/repo"
  git -C "$work/repo" remote add origin "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY.git"
  git -C "$work/repo" fetch --quiet --no-tags --filter=blob:none origin \
    "+refs/heads/$head:refs/remotes/origin/$head" "+refs/heads/$base:refs/remotes/origin/$base"
  git -C "$work/repo" checkout --quiet -B "$head" "refs/remotes/origin/$head"
  gh pr view "$number" --json body --jq .body >"$work/body.md"
  cp "$work/body.md" "$work/body.orig.md"

  local args=(--repo-dir "$work/repo" --config-file "$CONFIG_FILE" --changed-since "refs/remotes/origin/$base" --body-file "$work/body.md")
  python3 -I "$DEDUP" "${args[@]}"
  if ! python3 -I "$DEDUP" --check "${args[@]}"; then
    fail "dedup-merge-commits: duplicates remain in pull request #$number after the cleanup; nothing was pushed or edited"
  fi

  if [ -n "$(git -C "$work/repo" status --porcelain)" ]; then
    git -C "$work/repo" -c user.name="github-actions[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
      commit --quiet --all --message "$COMMIT_MESSAGE"
    # A normal push: if the branch moved meanwhile, the push is rejected.
    git -C "$work/repo" push --quiet origin "HEAD:refs/heads/$head"
    echo "pushed '$COMMIT_MESSAGE' to $head"
  else
    echo "no changelog change in $head"
  fi

  # The release notes of the GitHub release come from this body.
  if cmp --silent "$work/body.orig.md" "$work/body.md"; then
    echo "pull request #$number body already clean"
  else
    gh api --method PATCH "repos/$GITHUB_REPOSITORY/pulls/$number" --field "body=@$work/body.md" --silent
    echo "edited the body of pull request #$number"
  fi
  rm -rf "$work"
}

jq -r '.[] | [.number, .headBranchName, .baseBranchName] | @tsv' <<<"$PRS_JSON" |
  while IFS=$'\t' read -r number head base; do
    dedup_pull_request "$number" "$head" "$base"
  done
