# AGENTS.md — actions

Generic reusable GitHub Actions components (composite actions and reusable workflows) for any adopter. This repository is public. The rules for users of the repository are in [README.md](README.md). This file holds the rules for changing it.

Claude Code and Codex read this file; there is no `CLAUDE.md` in this repository.

## Language

All files, comments, commit messages and pull requests are in English.

## Public repository

- Everything here is public, including the git history, pull requests and issues. A secret, an internal reference or a client name that enters a commit stays in the history even after a fix.
- Never write an organization's internal value: project id, account id, bucket, data store, client name, price, personal e-mail, internal hostname or a link to a private repository or document.
- Report a vulnerability as described in [SECURITY.md](SECURITY.md), never in a public issue or pull request.

## Only generic components

- A component works unchanged in a fork owned by another organization. It never contains an organization name, project id, bucket, data store, client name, price, personal e-mail, `owner:` value or organization-specific default.
- Configuration reaches a component through inputs, and through caller variables named `<COMPONENT_IN_UPPER_SNAKE>_<INPUT_IN_UPPER_SNAKE>` (README, "Variables and secrets"). A component never reads another variable name.
- A secret reaches a reusable workflow through a secret declared in `on.workflow_call.secrets` (kebab-case, with `description` and `required`). The caller passes it with one explicit line. Never `secrets: inherit`.
- Component paths: every file under a top-level directory that holds an `action.yml` (`<component>/**`), and `.github/workflows/<component>.yml`. Internal workflows (`.github/workflows/_*.yml`) are the only files allowed to refer to the maintainer organization. Each such reference is guarded by `github.repository_owner`.
- A component reads each variable and secret by name (`vars.<NAME>`, `secrets.<name>`). Never index (`vars['<NAME>']`) or pass a whole context (`toJSON(vars)`, `toJSON(secrets)`).
- A component never calls a reusable workflow or action of a private repository.

## Before a component enters

- The code is written by the maintainers, or comes from a source under a license compatible with the Apache License 2.0. Third-party code keeps its copyright and license notices.
- The component holds no internal reference of any organization (see "Public repository").
- The component has the `README.md` described in the README, "Catalog", and its row in the catalog table.

## Rules for every workflow and action

- Third-party actions are pinned to a full 40-character commit SHA, with a `# vX.Y.Z` comment. Check the SHA with `gh api repos/<owner>/<repo>/git/ref/tags/<tag>`, and dereference annotated tags.
- `permissions: {}` at the top of every workflow. Every job declares its minimum permissions. Add a comment when a job needs more than `contents: read`. A reusable component workflow asks for no more than `contents: read`.
- A secret appears only in the `with:` or `env:` of the step that uses it, or in the `secrets:` block of a job that calls a reusable workflow. Never in a workflow-level or job-level `env:`.
- No `${{ }}` expression inside `run:`. Pass values through `env:` and quote them in the script.
- No long script inside YAML. A `run:` block only calls a script or holds a few lines of glue; logic lives in `<component>/scripts/`, with tests in `<component>/tests/` that run in CI.
- Scripts are called only from a composite action, through `${{ github.action_path }}/scripts/<name>`: a runner downloads the files of an action, but not the files of the repository that holds a reusable workflow. A reusable workflow stays thin and calls composite actions (unitary components); it never checks out this repository to reach a script.
- Triggers: only `pull_request`, `push` to `main` (`branches: [main]`), `workflow_dispatch` and `workflow_call`. Never `pull_request_target`, `workflow_run`, `issue_comment` or `schedule`.
- Workflow files use two-space indentation. `check-genericity` rejects any other indentation.
- `actions/checkout` always with `persist-credentials: false`.
- Every job has `timeout-minutes` and `runs-on: ubuntu-24.04`. A component job that holds a secret also has `concurrency`.
- No automatic approval of pull requests.
- Python code uses only the standard library, runs as `python3 -I` and is tested with `unittest` and fixtures.
- Inputs are validated before any write.
- Every reusable component workflow declares a boolean `dry-run` input: in dry-run mode the component validates and reports what it would do, without credentials and without writes.

## Gates

`_ci.yml` must be green before a change is delivered: `actionlint`, `zizmor` (online, `known-vulnerable-actions` included), `gitleaks`, `check-genericity`, `unit-tests` and the aggregated `ci-gate`, plus the dry-run jobs of each component.

- A new CI job is added to the `needs:` list of `ci-gate`. Nothing else in `ci-gate` changes.
- CI tools (actionlint, zizmor, gitleaks) run at an exact version, with a SHA-256 checksum, both set in `_ci.yml`. Dependabot does not update them. Whenever `_ci.yml` changes, check the latest release of each tool and its security advisories, and update the version and the checksum together. shellcheck comes from the `ubuntu-24.04` runner image.
- A new rule in `check-genericity.sh` gets a failing case in `test-check-genericity.sh`.
- A reusable component workflow calls its composite action with `uses: $/<component>`. actionlint 1.7.12 rejects that form, so the component adds to `.github/actionlint.yaml` an entry that ignores exactly that message for exactly its workflow file; nothing else is ignored there. `check-genericity` checks the `with:` of that step against the inputs of the action and of the workflow in place of actionlint; an input that only the workflow uses is listed in `WORKFLOW_ONLY_INPUTS` there.
- A new component gets a dry-run job in `_ci.yml` that calls its reusable workflow by `./` with `dry-run: true` on a fixture under `<component>/tests/fixtures/`, listed in the `needs:` of `ci-gate`, and its directory in `.github/dependabot.yml`. `check-genericity` fails a component without the `dry-run` input, the fixture or that job. `unit-tests` fails a component without a `tests/` folder.

Run the same checks locally before pushing:

```bash
actionlint
shellcheck .github/scripts/*.sh
zizmor --config zizmor.yml .        # needs GH_TOKEN for the online audits
gitleaks git --redact --no-banner .
bash .github/scripts/test-check-genericity.sh
bash .github/scripts/check-genericity.sh
bash .github/scripts/test-check-component-paths.sh
bash .github/scripts/test-move-major-tags.sh
bash .github/scripts/check-component-paths.sh origin/main HEAD
for tests in */tests; do python3 -I -B -m unittest discover -s "$tests"; done
```

## Commits and pull requests

- Conventional Commits. The scope is the component name (`feat(<component>): ...`). Use `ci`, `docs` or `chore` without a component scope for repository-wide changes.
- A breaking change of an input, output or secret is a major version (`!` or a `BREAKING CHANGE:` footer).
- Never write an issue reference (`#N`) in a commit message (subject, body or footer) or in a pull request title. The release tooling turns every such reference into "closes #N" and closes the issue when the release pull request merges. Link the issue with its full URL in the pull request description only.
- Never add a `Co-Authored-By` trailer.
- Pull requests merge with a merge commit, never squash. Never rewrite the history of a published branch: no rebase and no force-push.
- Versions are per component: one release-please package per component folder, tags `<component>-vX.Y.Z` and the major tag `<component>-vN` (README, "Versioning and releases"). A new component adds its package to `release-please-config.json`, with `component` set to the folder name, in the change that adds the component; release-please adds it to `.release-please-manifest.json` with its first release.
- A change to `.github/workflows/<component>.yml` comes with a change under `<component>/` in the same pull request: release-please reads only the folder. `check-component-paths.sh` fails the pull request otherwise. Dependabot pull requests are exempt: their `chore` commits never release.
- Releases come only from release-please. Do not edit a `CHANGELOG.md` or `.release-please-manifest.json` by hand, and do not create or move tags. The major tag `<component>-vN` is moved only by `_release.yml` (`.github/scripts/move-major-tags.sh`), with the release App token.
- `release-please-config.json` has no `extra-files` under `.github/workflows/`: the release GitHub App cannot write workflow files.

## Not in scope without an explicit decision

- A `NOTICE` file.
- Requiring a code owner review in a ruleset. `.github/CODEOWNERS` only requests the review of the code owner.
- Repository or organization settings, rulesets and GitHub App permissions.
- Moving tags other than the major tag `<component>-vN` of the release workflow.
