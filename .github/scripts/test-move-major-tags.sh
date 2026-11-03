#!/usr/bin/env bash
# Regression tests for move-major-tags.sh. A stub `gh` on PATH records the
# calls instead of reaching GitHub; each case checks the recorded calls and
# the exit status.
#
# Usage: test-move-major-tags.sh [repository-root]
set -euo pipefail

ROOT="$(cd "${1:-.}" && pwd)"
readonly ROOT
readonly SCRIPT="$ROOT/.github/scripts/move-major-tags.sh"
WORK="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/move-major-tags.XXXXXX")"
readonly WORK
trap 'rm -rf "$WORK"' EXIT
readonly SHA_A="1111111111111111111111111111111111111111"
readonly SHA_B="2222222222222222222222222222222222222222"
failures=0
cases=0

# Stub: logs each call; "git/ref/tags/<tag>" succeeds only for the tags
# listed in $STUB_EXISTING (space separated).
mkdir -p "$WORK/bin"
cat >"$WORK/bin/gh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$STUB_LOG"
for arg in "$@"; do
  case "$arg" in
    */git/ref/tags/*)
      tag="${arg##*/git/ref/tags/}"
      [[ " $STUB_EXISTING " == *" $tag "* ]] || exit 1
      ;;
  esac
done
exit 0
EOF
chmod +x "$WORK/bin/gh"

# Runs one case: $1 name, $2 expected status, $3 outputs JSON, $4 existing
# tags, $5 expected call log (newline separated).
run_case() {
  local name="$1" expected="$2" outputs="$3" existing="$4" calls="$5" actual log
  cases=$((cases + 1))
  log="$WORK/log.$cases"
  : >"$log"
  actual=0
  PATH="$WORK/bin:$PATH" STUB_LOG="$log" STUB_EXISTING="$existing" \
    RELEASE_OUTPUTS="$outputs" GITHUB_REPOSITORY="owner/repo" \
    bash "$SCRIPT" >/dev/null 2>&1 || actual=$?
  if [[ "$actual" -ne "$expected" ]]; then
    printf 'FAIL: %s: expected exit %s, got %s\n' "$name" "$expected" "$actual" >&2
    failures=$((failures + 1))
    return
  fi
  if [[ "$(<"$log")" != "$calls" ]]; then
    printf 'FAIL: %s: unexpected calls:\n%s\n' "$name" "$(<"$log")" >&2
    failures=$((failures + 1))
    return
  fi
  printf 'ok: %s\n' "$name"
}

run_case "nothing released" 0 '{"releases_created":"false"}' "" ""

run_case "new major tag is created" 0 \
  '{"paths_released":"[\"example-check\"]","example-check--tag_name":"example-check-v2.0.0","example-check--sha":"'"$SHA_A"'"}' \
  "" \
  "api repos/owner/repo/git/ref/tags/example-check-v2
api --method POST repos/owner/repo/git/refs -f ref=refs/tags/example-check-v2 -f sha=$SHA_A"

run_case "existing major tag is moved" 0 \
  '{"paths_released":"[\"example-check\"]","example-check--tag_name":"example-check-v2.1.3","example-check--sha":"'"$SHA_A"'"}' \
  "example-check-v2" \
  "api repos/owner/repo/git/ref/tags/example-check-v2
api --method PATCH repos/owner/repo/git/refs/tags/example-check-v2 -f sha=$SHA_A -F force=true"

run_case "pre-release moves nothing" 0 \
  '{"paths_released":"[\"example-check\"]","example-check--tag_name":"example-check-v1.0.0-rc.3","example-check--sha":"'"$SHA_A"'"}' \
  "" ""

run_case "two components, one pre-release" 0 \
  '{"paths_released":"[\"a-check\",\"b-sync\"]","a-check--tag_name":"a-check-v1.0.0-rc.4","a-check--sha":"'"$SHA_A"'","b-sync--tag_name":"b-sync-v3.0.1","b-sync--sha":"'"$SHA_B"'"}' \
  "" \
  "api repos/owner/repo/git/ref/tags/b-sync-v3
api --method POST repos/owner/repo/git/refs -f ref=refs/tags/b-sync-v3 -f sha=$SHA_B"

run_case "tag without component fails" 1 \
  '{"paths_released":"[\"example-check\"]","example-check--tag_name":"v2.0.0","example-check--sha":"'"$SHA_A"'"}' \
  "" ""

run_case "short SHA fails" 1 \
  '{"paths_released":"[\"example-check\"]","example-check--tag_name":"example-check-v2.0.0","example-check--sha":"abc123"}' \
  "" ""

printf '%s cases, %s failures\n' "$cases" "$failures"
[[ "$failures" -eq 0 ]]
