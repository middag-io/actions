# actions

Reusable GitHub Actions components: composite actions and reusable workflows that adopters call from their own repositories. The components are generic. Each adopter keeps its own configuration in its calling workflow and in its own variables and secrets. Some components do one task. Others combine several tasks.

Public repository, maintained by MIDDAG. Licensed under the [Apache License 2.0](LICENSE).

## Catalog

| Component | Kind | What it does                                        | Since |
| --------- | ---- | --------------------------------------------------- | ----- |
| [`cloudflare-pages-deploy`](cloudflare-pages-deploy/README.md) | composite action and reusable workflow | builds a static site and deploys it to Cloudflare Pages when its content hash changed; replaces the content served by the deployed branch, deletes nothing in the repository | `cloudflare-pages-deploy-v1.0.0` |
| [`cloudflare-r2-docs-publish`](cloudflare-r2-docs-publish/README.md) | composite action and reusable workflow | publishes a prepared documentation package to a Cloudflare R2 (or S3-compatible) bucket as an immutable version, with conditional writes; overwrites only `<package-name>/manifest.json`; deletes nothing | `cloudflare-r2-docs-publish-v1.0.0` |
| [`cloudflare-workers-check`](cloudflare-workers-check/README.md) | composite action and reusable workflow | validates a Cloudflare Workers project: npm ci, named npm scripts, optional `wrangler deploy --dry-run` | `cloudflare-workers-check-v1.0.0` |
| [`cloudflare-workers-deploy`](cloudflare-workers-deploy/README.md) | composite action and reusable workflow | builds a Node.js project and deploys its Worker with `wrangler deploy`; replaces the deployed version and the routes of the configuration, deletes plain-text variables missing from the configuration, applies its Durable Object migrations (a deleted class loses its data); deletes nothing in the repository | `cloudflare-workers-deploy-v1.0.0` |
| [`github-release-major-check`](github-release-major-check/README.md) | composite action and reusable workflow | checks that a pull request asks for a new MAJOR version only explicitly: fails on `!` or a `BREAKING CHANGE:` footer unless a commit of the pull request carries `Release-As: X.0.0` with `X` above the MAJOR of the package version in the release-please manifest of the base commit, and on a `Release-As:` footer that raises the MAJOR another way or does not move the version forward; reads the title and the non-merge commits of the pull request; publishes and deletes nothing | `github-release-major-check-v1.0.0` |
| [`go-module-scan`](go-module-scan/README.md) | composite action and reusable workflow | scans the packages of a Go module with `govulncheck` and fails only on a vulnerability whose severity is in `blocking-severities` (default `critical`); the Go vulnerability database has no severity, so a called vulnerable symbol is `critical` and an imported package or a required module is `low`; reports the rest; publishes and deletes nothing | `go-module-scan-v1.0.0` |
| [`moodle-host-check`](moodle-host-check/README.md) | composite action and reusable workflow | installs a throwaway Moodle of each version of a support matrix (one job per Moodle branch and PHP version, on an empty PostgreSQL service) and runs a PHP probe that loads the code under test inside it; publishes nothing; deletes nothing in the repository | `moodle-host-check-v1.0.0` |
| [`moodle-plugin-check`](moodle-plugin-check/README.md) | composite action and reusable workflow | validates a Moodle plugin in a change: `version.php`, the English language file, `composer validate`, `composer install` and the Composer scripts named by the caller, one job per PHP version; reads private Composer dependencies with a read-only GitHub App token; publishes and deletes nothing | `moodle-plugin-check-v1.0.0` |
| [`moodle-plugin-package`](moodle-plugin-package/README.md) | composite action and reusable workflow | packages a Moodle plugin tree as a deterministic installable ZIP after checking `version.php`; uploads it as a workflow artifact and, from the composite action, to the existing release of a tag; deletes only the release asset of the same name, which it replaces | `moodle-plugin-package-v1.0.0` |
| [`node-npm-scan`](node-npm-scan/README.md) | composite action and reusable workflow | audits the npm dependencies of `package-lock.json` with `npm audit` and fails only on an advisory whose severity is in `blocking-severities` (default `critical`); reports lower severities; publishes and deletes nothing | `node-npm-scan-v1.0.0` |
| [`php-composer-scan`](php-composer-scan/README.md) | composite action and reusable workflow | audits the installed Composer dependencies (or `composer.lock`) with `composer audit` and fails only on an advisory whose severity is in `blocking-severities` (default `critical`); reports lower severities and abandoned packages; publishes and deletes nothing | `php-composer-scan-v1.0.0` |
| [`php-composer-setup`](php-composer-setup/README.md) | composite action and reusable workflow | sets up PHP and Composer in the current job, writes the Composer credentials outside the checkout, restores and saves the Composer download cache and runs `composer install`; deletes nothing | `php-composer-setup-v1.0.0` |
| [`php-library-check`](php-library-check/README.md) | composite action and reusable workflow | validates a PHP library (a Composer package) in a change, one job per PHP version: `composer install`, a dependency audit that fails on `audit-blocking-severities` (default `critical`), and the static checks (`composer validate`, `dump-autoload --strict-psr`, the lint, PHPStan, PHP-CS-Fixer and Rector scripts) on the first PHP version, then the test script in every job; publishes and deletes nothing | `php-library-check-v1.0.0` |
| [`php-version-family-check`](php-version-family-check/README.md) | composite action and reusable workflow | checks a pull request against the window of a version family (packages that share MAJOR.MINOR): fails on `feat` while the window is closed, once the package version in the release-please manifest of the base commit reaches `family-from`, on `!` or a `BREAKING CHANGE:` footer outside a family MAJOR window, and on a `Release-As:` footer other than the open window's; a window counts only for pull requests into the default branch and when ahead of that version; reads the title and the non-merge commits of the pull request; publishes and deletes nothing | `php-version-family-check-v1.0.0` |
| [`release-please`](release-please/README.md) | composite action and reusable workflow | runs release-please in manifest mode with a GitHub App installation token limited to the calling repository: opens or updates the release pull request, then creates the tags and releases; deletes no branch, tag or file, force-pushes the release branch | `release-please-v1.0.0` |
| [`wp-plugin-check`](wp-plugin-check/README.md) | composite action and reusable workflow | validates a WordPress plugin in a change: `composer install` and the Composer scripts named by the caller, one job per PHP version, and optionally `npm ci`, `tsc --noEmit` and the npm scripts of its UI directory; publishes and deletes nothing | `wp-plugin-check-v1.0.0` |
| [`wp-plugin-release`](wp-plugin-release/README.md) | composite action and reusable workflow | builds the distribution ZIP of a WordPress plugin (optional UI build, production Composer install) and uploads it to the existing GitHub release of a tag with a GitHub App token; creates no tag or release; deletes only the release asset of the same name, which it replaces | `wp-plugin-release-v1.0.0` |
| [`wp-theme-check`](wp-theme-check/README.md) | composite action and reusable workflow | checks a WordPress theme with its own PHP CS Fixer, PHPStan and Rector, one job per PHP version; publishes nothing; deletes nothing | `wp-theme-check-v1.0.0` |

Every component has its own `README.md` next to its `action.yml`. That README lists what the component does, what it **deletes**, its inputs, outputs and secrets, the permissions the caller must grant, and a call example by the major tag `<component>-v1`, with the SHA form for a fork and a caller outside the maintainer organization.

## Naming convention

1. A component is named `{scope}-{object}-{verb}` and uses only `[a-z0-9-]`.
   - `{scope}` is the provider or ecosystem that the component touches. Closed list: `google-cloud`, `cloudflare`, `aws`, `github`, `docker`, `go`, `node`, `php`, `wp`, `moodle`.
   - `{object}` is the product, spelled out as its provider names it.
   - `{verb}` states the expected result. Closed list:

     | Verb       | Meaning                                           |
     | ---------- | ------------------------------------------------- |
     | `check`    | validates a change; publishes nothing             |
     | `build`    | builds an artifact                                |
     | `package`  | packages a built artifact                         |
     | `release`  | creates a version                                 |
     | `publish`  | uploads to a registry or store                    |
     | `deploy`   | ships to a running environment                    |
     | `sync`     | mirrors a source to a target, including deletions |
     | `dispatch` | triggers another workflow or system               |
     | `lint`     | reports style or correctness problems             |
     | `scan`     | looks for vulnerabilities or secrets              |
     | `setup` | prepares a tool or runtime in the current job for the steps after it; publishes nothing |

   - Abbreviations: only the widely recognized ones (`wp`, `aws`, `php`, `node`). Spell out products and platforms: `google-cloud`, never `gcp`; `cloudflare`, never `cf`.
   - Exception: a wrapper of a well-known tool keeps the tool name (for example `release-please`).
2. One name serves both forms of a component: the composite action at `<name>/action.yml` and the reusable workflow at `.github/workflows/<name>.yml`. GitHub does not accept subdirectories in `.github/workflows/`.
3. The `_` prefix marks an internal workflow of this repository (`_ci.yml`, `_release.yml`). Nothing outside this repository calls an internal workflow.
4. Interface names (inputs, outputs and secrets declared in `workflow_call`) are kebab-case and neutral: no organization, GitHub App or client name. Booleans use `type: boolean`. Every input has a `description` and either a neutral default or `required: true`.
5. Inside a file: `name:` in English with the provider spelled out; the job id is the verb; step names are imperative English; the concurrency group is `<name>-<key>`.
6. A component that needs a GitHub App takes the input `app-client-id` and the secret `app-private-key`.

Examples:

| Component                 | Reads as                                 |
| ------------------------- | ---------------------------------------- |
| `cloudflare-pages-deploy` | deploys a site to Cloudflare Pages       |
| `wp-plugin-check`         | validates a WordPress plugin in a change |

## Triggers

A workflow in this repository runs only on `pull_request`, `push` to `main`, `workflow_dispatch` and `workflow_call`. Never `pull_request_target`, `workflow_run`, `issue_comment` or `schedule`.

## Variables and secrets

A component reads configuration through inputs. A reusable workflow can also fill an input from a variable of the caller.

- **Names.** A variable or secret read by a component is named `<COMPONENT_IN_UPPER_SNAKE>_<INPUT_IN_UPPER_SNAKE>`. The prefix is the component, not the organization and not the provider. Example for a component `cloudflare-pages-deploy` with the input `project-name`: the variable `CLOUDFLARE_PAGES_DEPLOY_PROJECT_NAME`. A generic name such as `CLOUDFLARE_PROJECT_NAME` would collide with other uses of the same provider.
- **Three levels, most specific first.**
  1. An explicit input in the caller's `with:`.
  2. A variable of the caller's repository.
  3. A variable of the caller's organization.

  The reusable workflow declares the input default as `${{ vars.<NAME> }}`. In a called workflow, `vars` resolves against the caller. GitHub already prefers a repository variable over an organization variable with the same name.

- **Composite actions** receive values only through `with:`. The variable fallback lives in the reusable workflow.
- **Missing value.** When no input and no variable supply a required value, the component fails before any other step. The error names the input and the variable.
- **Secrets.** Never `secrets: inherit`. The caller passes each secret with one explicit line:

  ```yaml
  secrets:
    api-token: ${{ secrets.CLOUDFLARE_PAGES_DEPLOY_API_TOKEN }}
  ```

- **No adopter values.** A component never carries an adopter's value: no project, bucket, data store, organization name or client name, not even as a default.

## Referencing a component

Each component has its own version and its own release tags (see "Versioning and releases"). A reference names a release of the component you call, in one of three forms:

- the full 40-character commit SHA of a release tag, with the tag as a comment (examples below). This form never changes under the caller;
- the release tag `<component>-vX.Y.Z`, which never moves: releases are immutable and only the release automation creates tags;
- the major tag `<component>-vN`, which follows the latest stable release of major `N`. It exists only once the component has a stable release; a pre-release (`-rc.N`) is referenced by SHA or by its full tag.

Which forms a caller accepts is the caller organization's policy. A fork and a caller outside the maintainer organization pin the SHA:

```yaml
jobs:
  deploy:
    uses: <owner>/actions/.github/workflows/<component>.yml@<sha40> # <component>-vX.Y.Z
```

```yaml
steps:
  - uses: <owner>/actions/<component>@<sha40> # <component>-vX.Y.Z
```

- Use the tag of the component you call. A tag of another component does not tell which version of this component you get.
- Never `@main` or a branch.
- Find the SHA with `gh api repos/<owner>/actions/git/ref/tags/<component>-vX.Y.Z`. When the object type is `tag` (annotated tag), dereference it with `gh api repos/<owner>/actions/git/tags/<object-sha>` and use the commit SHA it points to.
- Only the release automation moves the major tag `<component>-vN`. A repository ruleset lets only the release App create, move or delete the tags `<component>-v*`.
- Third-party actions used inside this repository follow the SHA rule. Dependabot updates the SHA and the version comment together.
- Inside this repository, a component refers to another one only through the self-repository form (`$/<component>`) or a relative path (`./<component>`). It never refers to `<owner>/actions@...`.
- A component in this public repository never calls a component of a private repository.

## Versioning and releases

- Each component has its own SemVer version, managed by release-please from Conventional Commits. `release-please-config.json` has one package per component folder, and `.release-please-manifest.json` holds the current version of each component. A major version of one component never changes the version of another.
- Tags: `<component>-vX.Y.Z` for each release (immutable) and `<component>-vN` for the major tag, which the release automation moves to each release that is not a pre-release. Pre-releases (`-rc.N`) are never marked as latest. Only the release automation creates, moves or deletes a tag.
- release-please assigns a change to a component only through the files under the component folder (`<component>/`). A pull request that changes `.github/workflows/<component>.yml` must also change a file under `<component>/`; the `check-genericity` job enforces it on pull requests, except those of Dependabot, whose `chore` commits never release. The commit scope is the component name, and a pull request that changes one component has the scope of that component in its title.
- One release pull request groups the pending releases of every component. Each component has its own `<component>/CHANGELOG.md`.
- A breaking change of an input, output or secret is a major version of that component.
- Only release-please writes the changelogs and `.release-please-manifest.json`.

## Deprecation and support of older majors

- A new major of a component never removes the previous one. The previous major stays usable through its immutable release tags `<component>-vX.Y.Z` and through the SHAs of those tags.
- At least 30 days before a new major is released, the current major prints a deprecation warning in the workflow log that names the change and the release that will carry it.
- After a new major is released, the previous major gets only security fixes, for 6 months, and only when the component is used by a fork kept under a client contract. Other fixes and new features go only to the latest major.

## Forks

- Every component runs unchanged in a fork owned by another organization. Nothing in a component names an owner, an organization variable or an organization secret.
- Keep the original repository as the `upstream` remote and sync by release tag.
- Internal workflows can be guarded by repository owner. Release automation does not run in a fork.
- Callers in the fork owner's organization pin the fork's SHAs, not the upstream ones.

## Quality gates

`.github/workflows/_ci.yml` runs on every pull request and on every push to `main`. A pull request runs every job below, with the dry-run jobs only of the components it changes (all of them when it changes another file under `.github/`). The release pull request that changes only changelogs and `.release-please-manifest.json`, and a push to `main`, run only `actionlint`, `zizmor` and `gitleaks`:

| Job                | What it checks                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `actionlint`       | workflow syntax, expressions and embedded shell (shellcheck)                                                                                                                                                                                                                                                                                                                                                                                     |
| `zizmor`           | GitHub Actions security audits, online, including `known-vulnerable-actions`; any finding fails                                                                                                                                                                                                                                                                                                                                                  |
| `gitleaks`         | secrets in the full git history                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `check-genericity` | the rules of this README and of `AGENTS.md` that the other tools do not cover (`.github/scripts/check-genericity.sh`), after its regression tests (`.github/scripts/test-check-genericity.sh`); then that a change to `.github/workflows/<component>.yml` comes with a change under `<component>/` (`.github/scripts/check-component-paths.sh`), and the tests of that check, of the major tag script of the release workflow and of the CI plan |
| `unit-tests`       | the tests of every component (`<component>/tests/`), with fixtures and without cloud access                                                                                                                                                                                                                                                                                                                                                      |
| `changes`          | plans the run from the event and the changed files (`.github/scripts/ci-changes.sh`)                                                                                                                                                                                                                                                                                                                                                             |
| `ci-gate`          | aggregated required check; fails when any job above fails, or is skipped although the plan of `changes` runs it                                                                                                                                                                                                                                                                                                                                  |

The CI tools run at exact versions and are verified by SHA-256 checksum. Among the rules of `check-genericity`: no maintainer organization name in a component path, no reference to this repository by owner (`<owner>/actions`) and no reference to a private repository.

Every component is dry-run in CI before it can merge. `check-genericity` fails when a reusable component workflow does not declare a boolean `dry-run` input, has no fixture under `<component>/tests/fixtures/`, or has no job in `_ci.yml` that calls `./.github/workflows/<component>.yml` with `dry-run: true` and is listed in the `needs:` of `ci-gate`.

## Contributing

Contributions are welcome by pull request. Read [AGENTS.md](AGENTS.md) first: it holds the rules for changing this repository. Under section 5 of the Apache License 2.0, a contribution you submit is licensed under the same terms, unless you state otherwise.

## Security

See [SECURITY.md](SECURITY.md).

## License

Apache License 2.0. See [LICENSE](LICENSE).
