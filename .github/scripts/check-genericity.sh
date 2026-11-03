#!/usr/bin/env bash
# Checks the repository rules that actionlint and zizmor do not cover
# (README.md "Naming convention" and "Variables and secrets"; AGENTS.md).
#
# Usage: check-genericity.sh [repository-root]
#
# A component directory is a top-level directory that holds an action.yml
# or action.yaml at any depth. Component paths: every file under a component
# directory, and .github/workflows/<component>.y*ml (no "_" prefix).
# <component>/CHANGELOG.md is not a component path: release-please writes it,
# and its compare and commit links name the repository and its owner.
# Internal workflows (.github/workflows/_*.y*ml) and the root documents are
# not component paths.
#
# The structural checks read workflow files written with two-space
# indentation. Any other indentation fails the check.
#
# Every finding goes through report(), which must run in this shell so that
# it can set the exit status. Feed report_matches with "< <(...)", never
# with a pipe: the last element of a pipe runs in a subshell.
set -euo pipefail

cd "${1:-.}"

readonly WORKFLOW_DIR=".github/workflows"
readonly COMPONENT_NAME_RE='^(google-cloud|cloudflare|aws|github|docker|node|php|wp|moodle)-[a-z0-9]+(-[a-z0-9]+)*-(check|build|package|release|publish|deploy|sync|dispatch|lint|scan|setup)$'
# Wrappers of well-known tools keep the tool name (README, Naming convention).
readonly WRAPPER_NAMES=" release-please "
# Lines that hold an expression: "${{ ... }}" or an "if:" key.
readonly EXPRESSION_LINE_RE='\$\{\{|^[[:space:]]*(-[[:space:]]+)?if:'
# "vars" or "secrets" not followed by ".<name>": index, bulk or argument use.
readonly DYNAMIC_CONTEXT_RE='(^|[^A-Za-z0-9_.-])(vars|secrets)[[:space:]]*([^.:A-Za-z0-9_[:space:]-]|$)'
status=0

# Prints a GitHub annotation and marks the run as failed.
report() {
  local file="$1" line="$2" message="$3"
  printf '::error file=%s,line=%s::%s\n' "$file" "$line" "$message"
  status=1
}

# Reads "file:line:text" lines from grep and reports each one.
report_matches() {
  local message="$1" file line _rest
  while IFS=: read -r file line _rest; do
    report "$file" "$line" "$message"
  done
}

# Prints the organization name pattern. It is assembled so that this
# script does not contain it.
organization_pattern() {
  printf '%s%s' 'mid' 'dag'
}

# Lists every workflow file, internal ones included.
list_workflows() {
  find "$WORKFLOW_DIR" -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \) 2>/dev/null | sort
}

# Lists the component workflows (no "_" prefix).
list_component_workflows() {
  list_workflows | grep -v "^${WORKFLOW_DIR}/_" || true
}

# Lists the component directories, one per line.
list_component_dirs() {
  find . -path './.*' -prune -o -type f \( -name 'action.yml' -o -name 'action.yaml' \) -print \
    | sed 's|^\./||' | grep '/' | sed 's|/.*$||' | sort -u || true
}

# Lists component files, one per line, without a leading "./". The
# changelog that release-please writes at the root of a component is left
# out (see the header).
list_component_files() {
  local dir
  while IFS= read -r dir; do
    find "$dir" -type f ! -path "$dir/CHANGELOG.md" | sort
  done < <(list_component_dirs)
  list_component_workflows
}

# Lists every workflow and action metadata file.
list_definitions() {
  list_workflows
  find . -path './.*' -prune -o -type f \( -name 'action.yml' -o -name 'action.yaml' \) -print | sed 's|^\./||' | sort
}

# Prints the component that owns a component file.
component_of() {
  local file="$1"
  case "$file" in
    "$WORKFLOW_DIR"/*) basename "${file%.*}" ;;
    *) printf '%s\n' "${file%%/*}" ;;
  esac
}

# Item 13: no maintainer organization name in a component path, neither in
# the file names nor in the file contents.
check_organization_name() {
  local pattern file
  pattern="$(organization_pattern)"
  for file in "$@"; do
    if printf '%s\n' "$file" | grep -q -i -F -- "$pattern"; then
      report "$file" 1 "organization name in a component file or directory name; components stay generic"
    fi
  done
  report_matches "organization name in a component file; components stay generic" \
    < <(grep -H -n -a -i -F -- "$pattern" "$@" 2>/dev/null || true)
}

# Naming convention: {scope}-{object}-{verb} for every component directory
# and component workflow, or a wrapper name.
check_component_name() {
  local path="$1" name="$2"
  [[ "$WRAPPER_NAMES" == *" $name "* ]] && return 0
  [[ "$name" =~ $COMPONENT_NAME_RE ]] && return 0
  report "$path" 1 "component name ${name} does not follow {scope}-{object}-{verb} (README, Naming convention)"
}

check_component_names() {
  local entry name
  while IFS= read -r entry; do
    check_component_name "$entry" "$entry"
  done < <(list_component_dirs)
  while IFS= read -r entry; do
    name="$(basename "$entry")"
    check_component_name "$entry" "${name%.*}"
  done < <(list_component_workflows)
}

# Item 14: components refer to each other only by "\$/" or "./". The owner
# must start the match, so that a URL such as github.com/actions/... (a
# host, not an owner) is not a reference to this repository.
check_self_reference() {
  report_matches "component refers to an owner/actions path; use \$/<component> or ./<component>" \
    < <(grep -H -n -E -- '(^|[^A-Za-z0-9_./-])[A-Za-z0-9_-]+/actions[/@]' "$@" 2>/dev/null || true)
}

# Public repository: a component never calls a component of a private
# repository (README, "Referencing a component").
check_private_reference() {
  report_matches "component refers to a private repository; a public component never calls one" \
    < <(grep -H -n -E -- '[A-Za-z0-9_.-]+/(actions-private|\.github-private)([^A-Za-z0-9_.-]|$)' "$@" 2>/dev/null || true)
}

# Item 13 and the variable rule: a component reads only variables and
# secrets named <COMPONENT_IN_UPPER_SNAKE>_<INPUT>, its own kebab-case
# secrets, or GITHUB_TOKEN, and always by name.
check_context_names() {
  local file prefix
  for file in "$@"; do
    prefix="$(component_of "$file" | tr '[:lower:]-' '[:upper:]_')_"
    report_matches "variable or secret name outside the component convention (${prefix}<INPUT>)" < <(
      grep -n -o -E '(vars|secrets)\.[A-Za-z0-9_-]+' "$file" 2>/dev/null \
        | grep -v -E ":(vars|secrets)\.${prefix}[A-Z0-9_]+$" \
        | grep -v -E ':secrets\.([a-z0-9-]+|GITHUB_TOKEN)$' \
        | sed "s|^|${file}:|" || true
    )
    report_matches "dynamic or bulk access to vars or secrets is forbidden in components; read each value by name" < <(
      grep -n -E -- "$EXPRESSION_LINE_RE" "$file" 2>/dev/null \
        | grep -E -- "$DYNAMIC_CONTEXT_RE" \
        | sed "s|^|${file}:|" || true
    )
  done
}

# Items 5 and 17: no inherited secrets and no automatic approval.
check_forbidden_patterns() {
  local approve
  approve="$(printf '%s%s' '--app' 'rove')"
  report_matches "secrets: inherit is forbidden; pass each secret explicitly" \
    < <(grep -H -n -E -- 'secrets:[[:space:]]*inherit' "$@" 2>/dev/null || true)
  report_matches "automatic pull request approval is forbidden" \
    < <(grep -H -n -E -- "${approve}|event[\"']?[[:space:]]*[=:][[:space:]]*[\"']?APPROVE" "$@" 2>/dev/null || true)
}

# Item 12: every job runs on ubuntu-24.04.
check_runner() {
  report_matches "runs-on must be ubuntu-24.04" < <(
    grep -H -n -E '^[[:space:]]+runs-on:' "$@" 2>/dev/null \
      | grep -v -E "runs-on:[[:space:]]*[\"']?ubuntu-24\.04[\"']?[[:space:]]*(#.*)?$" || true
  )
}

# Items 3, 4, 5, 6, 9 and 11: workflow structure. Prints
# "file<TAB>line<TAB>message".
# shellcheck disable=SC2016 # awk program, not shell: no expansion wanted.
readonly STRUCTURE_AWK='
BEGIN {
  want = -1
  split("pull_request push workflow_dispatch workflow_call", names, " ")
  for (i in names) allowed[names[i]] = 1
}
function indent_of(s) { match(s, /[^ ]/); return RSTART - 1 }
function err(n, msg) { printf "%s\t%d\t%s\n", FILENAME, n, msg }
function strip(s) { sub(/[[:space:]]+#.*$/, "", s); sub(/[[:space:]]+$/, "", s); return s }
function unquote(s) { gsub(/^[[:space:]]+|[[:space:]]+$/, "", s); gsub(/["\047]/, "", s); return s }
function key_of(s) { sub(/:.*$/, "", s); return unquote(s) }
function value_of(s) { if (!sub(/^[^:]*:[[:space:]]*/, "", s)) return ""; return s }
function has_secret(s) { return s ~ /(^|[^A-Za-z0-9_.-])secrets[[:space:]]*(\.|\[|\)|\})/ }
function check_event(name, n) {
  if (!(name in allowed)) err(n, "trigger " name " is not allowed; use pull_request, push to main, workflow_dispatch or workflow_call")
  else if (name == "push") { in_push = 1; push_line = n; push_main = push_other = in_branches = 0 }
}
function close_push() {
  if (!in_push) return
  if (!push_main || push_other) err(push_line, "push runs only on branches: [main]")
  in_push = 0
}
function add_branches(list, count, i) {
  gsub(/[\[\]]/, "", list)
  count = split(list, items, ",")
  for (i = 1; i <= count; i++) { if (unquote(items[i]) == "main") push_main = 1; else push_other = 1 }
}
function on_inline(v, count, i) {
  if (v == "") return
  gsub(/[\[\]]/, "", v)
  count = split(v, items, ",")
  for (i = 1; i <= count; i++) { check_event(unquote(items[i]), FNR); close_push() }
}
function close_job() {
  if (job == "") return
  if (!job_perms) err(job_line, "job " job " declares no permissions")
  if (!job_uses && !job_timeout) err(job_line, "job " job " has no timeout-minutes")
  if (!job_uses && !job_runs_on) err(job_line, "job " job " has no runs-on")
  if (component && job_secrets && !job_concurrency) err(job_line, "component job " job " uses a secret but has no concurrency")
  job = ""
}
function close_decl() {
  if (decl == "") return
  if (!decl_desc) err(decl_line, decl_kind " " decl " has no description")
  if (decl_kind == "secret" && !decl_required) err(decl_line, "secret " decl " does not state required")
  if (decl_kind == "input" && !decl_default && !decl_required_true) err(decl_line, "input " decl " has neither a default nor required: true")
  decl = ""
}
/^[[:space:]]*(#|$)/ { next }
{
  ind = indent_of($0); line = $0; sub(/^ +/, "", line); code = strip(line)
  # The first child of a structural key sits exactly two spaces deeper.
  if (want >= 0) { if (ind > want - 2 && ind != want) err(FNR, "unsupported indentation; use two spaces"); want = -1 }
  if (in_perm && ind > perm_ind) {
    if (component && code !~ /^contents:[[:space:]]*read$/) err(FNR, "component workflows grant at most contents: read")
    next
  }
  in_perm = 0
  if (code ~ /^permissions:/) {
    value = value_of(code)
    if (value == "") { in_perm = 1; perm_ind = ind }
    else if (component && value != "{}") err(FNR, "component workflows grant at most contents: read")
  }
  if (ind == 0) {
    close_decl(); close_job(); close_push()
    section = key_of(code)
    if (code ~ /^permissions:[[:space:]]*\{\}$/) top_perms = 1
    if (section == "on") { seen_on = 1; on_inline(value_of(code)); want = 2 }
    else if (section == "jobs") { seen_jobs = 1; want = 2 }
    else if (has_secret(code)) err(FNR, "secret outside a step (MSR-013.R4(c))")
    next
  }
  if (section != "on" && section != "jobs" && has_secret(code)) err(FNR, "secret outside a step (MSR-013.R4(c))")
  if (section == "jobs") {
    if (ind == 2) {
      close_job(); job = key_of(code); job_line = FNR; job_count++; want = 4; job_key = ""
      job_perms = job_uses = job_timeout = job_runs_on = job_secrets = job_concurrency = 0
    } else if (ind == 4 && job != "" && code !~ /^- /) {
      job_key = key_of(code)
      if (job_key == "permissions") job_perms = 1
      if (job_key == "uses") job_uses = 1
      if (job_key == "timeout-minutes") job_timeout = 1
      if (job_key == "runs-on") job_runs_on = 1
      if (job_key == "concurrency") job_concurrency = 1
    }
    if (job != "" && has_secret(code)) {
      job_secrets = 1
      # Outside steps, only the secrets: block of a job that calls a reusable workflow may hold a secret.
      if (job_key != "steps" && job_key != "secrets") err(FNR, "secret outside a step (MSR-013.R4(c))")
    }
  }
  if (section == "on") {
    if (ind == 2) {
      close_decl(); close_push(); kind = ""; call = 0
      if (code ~ /^- /) { event = unquote(substr(code, 3)); check_event(event, FNR); close_push(); next }
      event = key_of(code); check_event(event, FNR); call = (event == "workflow_call"); want = 4
      if (in_push && value_of(code) !~ /^(null|~)?$/) err(FNR, "write the push trigger in block style")
    } else if (in_push && ind >= 4) {
      if (in_branches && code ~ /^- /) { add_branches(substr(code, 3)); next }
      if (ind != 4) next
      in_branches = 0; push_key = key_of(code)
      if (push_key == "branches") { in_branches = 1; add_branches(value_of(code)) }
      else if (push_key != "paths" && push_key != "paths-ignore") err(FNR, "push runs only on branches: [main]; " push_key " is not allowed")
    } else if (call && ind == 4) {
      close_decl(); kind = ""; want = 6
      if (code ~ /^secrets:/) kind = "secret"
      if (code ~ /^inputs:/) kind = "input"
    } else if (kind != "" && ind == 6) {
      close_decl(); decl = key_of(code); decl_kind = kind; decl_line = FNR; want = 8
      decl_desc = decl_required = decl_required_true = decl_default = 0
    } else if (decl != "" && ind == 8) {
      if (code ~ /^description:/) decl_desc = 1
      if (code ~ /^required:/) decl_required = 1
      if (code ~ /^required:[[:space:]]*true$/) decl_required_true = 1
      if (code ~ /^default:/) decl_default = 1
    }
  }
}
END {
  close_decl(); close_job(); close_push()
  if (!top_perms) err(1, "workflow has no top-level permissions: {}")
  if (!seen_on) err(1, "workflow has no top-level on: key")
  if (!seen_jobs || !job_count) err(1, "workflow has no job: expected a top-level jobs: key with two-space indentation")
}
'

check_workflow_structure() {
  local file component file_name line message
  for file in "$@"; do
    component=1
    [[ "$(basename "$file")" == _* ]] && component=0
    while IFS=$'\t' read -r file_name line message; do
      report "$file_name" "$line" "$message"
    done < <(awk -v component="$component" "$STRUCTURE_AWK" "$file")
  done
}

# Wiring of a component workflow to its composite action. actionlint does
# not check the with: keys of a "uses: \$/<component>" step, because it
# does not parse that form (.github/actionlint.yaml). Reads the action
# metadata, then the workflow; prints "file<TAB>line<TAB>message" when a
# with: key is not an input of the action, or when an input of the workflow
# (other than WORKFLOW_ONLY_INPUTS) is not passed to the action. Remove the
# first half when actionlint accepts "\$/".
# shellcheck disable=SC2016 # awk program, not shell: no expansion wanted.
readonly WIRING_AWK='
function indent_of(s) { match(s, /[^ ]/); return RSTART - 1 }
function strip(s) { sub(/[[:space:]]+#.*$/, "", s); sub(/[[:space:]]+$/, "", s); return s }
function unquote(s) { gsub(/^[[:space:]]+|[[:space:]]+$/, "", s); gsub(/["\047]/, "", s); return s }
function key_of(s) { sub(/^-[[:space:]]+/, "", s); sub(/:.*$/, "", s); return unquote(s) }
function value_of(s) { sub(/^-[[:space:]]+/, "", s); if (!sub(/^[^:]*:[[:space:]]*/, "", s)) return ""; return unquote(s) }
function err(n, msg) { printf "%s\t%d\t%s\n", FILENAME, n, msg }
function step_key(code, n, key) {
  key = key_of(code)
  in_with = 0
  if (key == "uses") step_uses = value_of(code)
  if (key == "with") {
    in_with = 1
    if (value_of(code) != "") err(n, "write the with: of " target " in block style")
  }
}
function close_step(k) {
  if (step_uses == target) {
    calls++
    for (k in with_line) {
      if (!(k in action_input)) err(with_line[k], "with: key " k " of " target " is not an input of " action_file)
      passed[k] = 1
    }
  }
  split("", with_line); step_uses = ""; in_with = 0
}
FNR == NR {
  if ($0 ~ /^[[:space:]]*(#|$)/) next
  ind = indent_of($0); code = strip(substr($0, ind + 1))
  if (ind == 0) { in_inputs = (code == "inputs:"); next }
  if (in_inputs && ind == 2) action_input[key_of(code)] = 1
  next
}
/^[[:space:]]*(#|$)/ { next }
{
  ind = indent_of($0); code = strip(substr($0, ind + 1))
  if (ind == 0) { close_step(); in_steps = 0; section = key_of(code); next }
  if (section == "on") {
    if (ind == 2) { in_call = (key_of(code) == "workflow_call"); in_wf_inputs = 0 }
    else if (in_call && ind == 4) in_wf_inputs = (key_of(code) == "inputs")
    else if (in_wf_inputs && ind == 6) workflow_input[key_of(code)] = FNR
    next
  }
  if (section != "jobs") next
  if (code ~ /^steps:/) { close_step(); in_steps = 1; steps_ind = ind; item_ind = -1; next }
  if (!in_steps) next
  if (item_ind < 0) {
    if (code ~ /^- / && ind >= steps_ind) item_ind = ind
    else { in_steps = 0; next }
  }
  if (ind < item_ind || (ind == item_ind && code !~ /^- /)) { close_step(); in_steps = 0; next }
  if (ind == item_ind) { close_step(); step_key(code, FNR); next }
  if (ind == item_ind + 2) { step_key(code, FNR); next }
  if (in_with && ind == item_ind + 4) with_line[key_of(code)] = FNR
}
END {
  close_step()
  if (!calls) exit
  for (k in workflow_input) {
    if (index(exempt, " " k " ") || index(exempt, " " component ":" k " ")) continue
    if (!(k in passed)) err(workflow_input[k], "input " k " of the reusable workflow is not passed to " target)
  }
}
'

# Inputs of the reusable workflow that only the workflow uses: a bare name
# applies to every component, <component>:<input> to that component only.
readonly WORKFLOW_ONLY_INPUTS=" timeout-minutes cloudflare-r2-docs-publish:artifact-name cloudflare-workers-deploy:github-environment moodle-plugin-check:php-versions wp-plugin-check:php-versions wp-theme-check:php-versions "

check_component_wiring() {
  local file component action file_name line message
  for file in "$@"; do
    component="$(component_of "$file")"
    action=""
    for action in "$component/action.yml" "$component/action.yaml"; do
      [[ -f "$action" ]] && break
    done
    [[ -f "$action" ]] || continue
    while IFS=$'\t' read -r file_name line message; do
      report "$file_name" "$line" "$message"
    done < <(awk -v target="\$/${component}" -v component="$component" -v action_file="$action" -v exempt="$WORKFLOW_ONLY_INPUTS" \
      "$WIRING_AWK" "$action" "$file")
  done
}

# Dry-run gate: every component workflow declares a boolean dry-run input,
# and the CI workflow has a job that calls it by "./" with dry-run: true,
# listed in the needs: of the aggregated gate job, on a fixture under
# <component>/tests/fixtures/. A component without it fails here.
readonly CI_WORKFLOW="${WORKFLOW_DIR}/_ci.yml"
readonly GATE_JOB="ci-gate"

# Prints "line<TAB>type" of the dry-run input of a reusable workflow, or
# nothing when the workflow declares no such input.
# shellcheck disable=SC2016 # awk program, not shell: no expansion wanted.
readonly DRY_RUN_INPUT_AWK='
function indent_of(s) { match(s, /[^ ]/); return RSTART - 1 }
function strip(s) { sub(/[[:space:]]+#.*$/, "", s); sub(/[[:space:]]+$/, "", s); return s }
function unquote(s) { gsub(/^[[:space:]]+|[[:space:]]+$/, "", s); gsub(/["\047]/, "", s); return s }
function key_of(s) { sub(/:.*$/, "", s); return unquote(s) }
function value_of(s) { if (!sub(/^[^:]*:[[:space:]]*/, "", s)) return ""; return unquote(s) }
/^[[:space:]]*(#|$)/ { next }
{
  ind = indent_of($0); code = strip(substr($0, ind + 1))
  if (ind == 0) { section = key_of(code); call = inputs = in_decl = 0; next }
  if (section != "on") next
  if (ind == 2) { call = (key_of(code) == "workflow_call"); inputs = in_decl = 0 }
  else if (call && ind == 4) { inputs = (key_of(code) == "inputs"); in_decl = 0 }
  else if (inputs && ind == 6) { in_decl = (key_of(code) == "dry-run"); if (in_decl) line = FNR }
  else if (in_decl && ind == 8 && key_of(code) == "type") type = value_of(code)
}
END { if (line) printf "%d\t%s\n", line, type }
'

# Reads the CI workflow. Prints "job<TAB>name<TAB>line<TAB>uses<TAB>dry-run"
# for every job, and "need<TAB>name" for every entry in the needs: of the
# gate job.
# shellcheck disable=SC2016 # awk program, not shell: no expansion wanted.
readonly CI_JOBS_AWK='
function indent_of(s) { match(s, /[^ ]/); return RSTART - 1 }
function strip(s) { sub(/[[:space:]]+#.*$/, "", s); sub(/[[:space:]]+$/, "", s); return s }
function unquote(s) { gsub(/^[[:space:]]+|[[:space:]]+$/, "", s); gsub(/["\047]/, "", s); return s }
function key_of(s) { sub(/:.*$/, "", s); return unquote(s) }
function value_of(s) { if (!sub(/^[^:]*:[[:space:]]*/, "", s)) return ""; return unquote(s) }
function close_job() { if (job != "") printf "job\t%s\t%d\t%s\t%s\n", job, job_line, uses, dry; job = "" }
function add_needs(list, count, i) {
  gsub(/[\[\]]/, "", list)
  count = split(list, items, ",")
  for (i = 1; i <= count; i++) if (unquote(items[i]) != "") printf "need\t%s\n", unquote(items[i])
}
/^[[:space:]]*(#|$)/ { next }
{
  ind = indent_of($0); code = strip(substr($0, ind + 1))
  if (ind == 0) { close_job(); section = key_of(code); next }
  if (section != "jobs") next
  if (ind == 2) { close_job(); job = key_of(code); job_line = FNR; uses = ""; dry = "false"; key = ""; next }
  if (ind == 4) {
    key = key_of(code)
    if (key == "uses") uses = value_of(code)
    if (key == "needs" && job == gate) add_needs(value_of(code))
    next
  }
  if (ind == 6 && key == "with" && key_of(code) == "dry-run") dry = value_of(code)
  if (ind == 6 && key == "needs" && job == gate && code ~ /^- /) add_needs(substr(code, 3))
}
END { close_job() }
'

check_dry_run_gate() {
  local -a workflows=("$@") ci_jobs=() gate_needs=()
  local file component decl_line decl_type kind name line uses dry entry job found
  if [[ ! -f "$CI_WORKFLOW" ]]; then
    report "$CI_WORKFLOW" 1 "${CI_WORKFLOW} is missing; every component needs a dry-run job there"
    return
  fi
  while IFS=$'\t' read -r kind name line uses dry; do
    case "$kind" in
      job) ci_jobs+=("${name}"$'\t'"${line}"$'\t'"${uses}"$'\t'"${dry}") ;;
      need) gate_needs+=("$name") ;;
    esac
  done < <(awk -v gate="$GATE_JOB" "$CI_JOBS_AWK" "$CI_WORKFLOW")

  for file in "${workflows[@]}"; do
    component="$(component_of "$file")"
    IFS=$'\t' read -r decl_line decl_type < <(awk "$DRY_RUN_INPUT_AWK" "$file") || true
    if [[ -z "${decl_line:-}" ]]; then
      report "$file" 1 "component ${component} declares no dry-run input; add a boolean dry-run input under on.workflow_call.inputs (no credentials, no writes)"
    elif [[ "$decl_type" != boolean ]]; then
      report "$file" "$decl_line" "dry-run input of ${component} must be type: boolean"
    fi
    if ! find "$component/tests/fixtures" -type f -print -quit 2>/dev/null | grep -q .; then
      report "$file" 1 "component ${component} has no dry-run fixture; add one under ${component}/tests/fixtures/"
    fi

    job=""
    for entry in "${ci_jobs[@]+"${ci_jobs[@]}"}"; do
      IFS=$'\t' read -r name line uses dry <<<"$entry"
      if [[ "$uses" == "./${file}" && "$dry" == true ]]; then
        job="$name"
        break
      fi
    done
    if [[ -z "$job" ]]; then
      report "$CI_WORKFLOW" 1 "component ${component} has no dry-run job; add a job that calls ./${file} with dry-run: true and list it in the needs: of ${GATE_JOB}"
      continue
    fi
    found=0
    for name in "${gate_needs[@]+"${gate_needs[@]}"}"; do
      [[ "$name" == "$job" ]] && found=1 && break
    done
    if ((found == 0)); then
      report "$CI_WORKFLOW" "$line" "dry-run job ${job} of ${component} is not in the needs: of ${GATE_JOB}"
    fi
  done
}

main() {
  local -a components=() definitions=() workflows=() scanned=()
  local entry
  while IFS= read -r entry; do components+=("$entry"); done < <(list_component_files)
  while IFS= read -r entry; do definitions+=("$entry"); done < <(list_definitions)
  while IFS= read -r entry; do workflows+=("$entry"); done < <(list_workflows)
  # Definitions plus component files other than Markdown, which may name
  # the forbidden patterns in prose.
  while IFS= read -r entry; do scanned+=("$entry"); done < <(
    printf '%s\n' "${definitions[@]+"${definitions[@]}"}" "${components[@]+"${components[@]}"}" \
      | grep -v -E '(^$|\.md$)' | sort -u || true
  )

  check_component_names
  if ((${#components[@]} > 0)); then
    check_organization_name "${components[@]}"
    check_self_reference "${components[@]}"
    check_private_reference "${components[@]}"
    check_context_names "${components[@]}"
  fi
  if ((${#scanned[@]} > 0)); then
    check_forbidden_patterns "${scanned[@]}"
  fi
  if ((${#definitions[@]} > 0)); then
    check_runner "${definitions[@]}"
  fi
  if ((${#workflows[@]} > 0)); then
    check_workflow_structure "${workflows[@]}"
  fi
  while IFS= read -r entry; do
    check_component_wiring "$entry"
  done < <(list_component_workflows)
  local -a component_workflows=()
  while IFS= read -r entry; do component_workflows+=("$entry"); done < <(list_component_workflows)
  if ((${#component_workflows[@]} > 0)); then
    check_dry_run_gate "${component_workflows[@]}"
  fi

  if ((status == 0)); then
    printf 'check-genericity: %d component files, %d definitions, %d workflows checked; no finding.\n' \
      "${#components[@]}" "${#definitions[@]}" "${#workflows[@]}"
  fi
  return "$status"
}

main
