# AGENTS.md — actions

Generic reusable GitHub Actions components (composite actions and reusable workflows) for any adopter. This repository is public. The rules for users of the repository are in [README.md](README.md). This file holds the rules for changing it.

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
- Workflow files use two-space indentation.
- `actions/checkout` always with `persist-credentials: false`.
- Every job has `timeout-minutes` and `runs-on: ubuntu-24.04`. A component job that holds a secret also has `concurrency`.
- No automatic approval of pull requests.
- Python code uses only the standard library, runs as `python3 -I` and is tested with `unittest` and fixtures.
- Inputs are validated before any write.

## Gates

This repository has no CI yet. The CI workflow, its aggregated required check and the release configuration enter before the first component. Until then, every pull request records in its description the local checks that ran: Markdown lint, and a search of the diff, the title and the description for secrets and internal references.

## Commits and pull requests

- Conventional Commits. The scope is the component name (`feat(<component>): ...`). Use `ci`, `docs` or `chore` without a component scope for repository-wide changes.
- A breaking change of an input, output or secret is a major version (`!` or a `BREAKING CHANGE:` footer).
- Never write an issue reference (`#N`) in a commit message (subject, body or footer) or in a pull request title. The release tooling turns every such reference into "closes #N" and closes the issue when the release pull request merges. Link the issue with its full URL in the pull request description only.
- Never add a `Co-Authored-By` trailer.
- Pull requests merge with a merge commit, never squash. Never rewrite the history of a published branch: no rebase and no force-push.
- Releases come only from release-please. Do not edit `CHANGELOG.md` or the release manifest by hand, and do not create tags.

## Not in scope without an explicit decision

- `CODEOWNERS` and a `NOTICE` file.
- Repository or organization settings, rulesets and GitHub App permissions.
- Moving tags.
