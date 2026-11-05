# cloudflare-workers-check

Validates a Cloudflare Workers project in a change. Needs no Cloudflare credentials and publishes nothing.

Two forms, one name:

- reusable workflow `.github/workflows/cloudflare-workers-check.yml`: checks out the calling repository and runs one job;
- composite action `cloudflare-workers-check/action.yml`: checks the project in a job of the caller, on a checkout the caller made.

## What it does

1. Validates the inputs and the project, before anything else:
   - `path` is a directory inside the checkout that holds `package.json` (a JSON object) and `package-lock.json`;
   - the Wrangler configuration exists: the file of `wrangler-config` (inside `path`), or else the first of `wrangler.json`, `wrangler.jsonc`, `wrangler.toml` at `path`;
   - every script of `npm-scripts` exists in `package.json`; a script cannot be in both `npm-scripts` and `npm-scripts-if-present`;
   - with `wrangler-dry-run`, `wrangler` is in the `dependencies` or `devDependencies` of `package.json`.
2. Plans the commands: `npm ci`, then `npm run <name>` for each script of `npm-scripts-if-present` that `package.json` defines (the others are skipped with a message), then `npm run <name>` for each script of `npm-scripts`, in order. With `wrangler-dry-run`, the plan ends with `npx --no-install wrangler deploy --dry-run --config <wrangler-config>`: Wrangler bundles the Worker and checks its configuration without uploading it.
3. Audits `package-lock.json` with `npm audit` through the [`node-npm-scan`](../node-npm-scan/README.md) action of the same release: an advisory whose severity is in `audit-blocking-severities` fails the job; lower severities are warnings. No input turns the audit off. In a dry run, `node-npm-scan` only validates and prints its command.
4. Outside a dry run: sets up Node.js (with the npm cache keyed on `package-lock.json`) and runs the planned commands in the project directory. The first failing command fails the job.

The commands run without a shell, as argument lists. `npm-scripts` and `npm-scripts-if-present` take script names only, so an input cannot add a command. `npx --no-install` runs the Wrangler version of the lock file and never downloads one.

In a dry run (`dry-run: true`), only steps 1 and 2 run, and `node-npm-scan` runs as a dry run: no Node.js set-up, no npm run, no audit. The job prints the commands it would run.

## What it deletes

Nothing. The component publishes nothing and deploys nothing. `npm ci` deletes `node_modules/` in the checkout of the job before it installs, and the npm scripts and Wrangler may write build output there; the checkout ends with the job.

## Inputs

| Input                    | Workflow      | Action               | Default           | Meaning                                                                                                                       |
| ------------------------ | ------------- | -------------------- | ----------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `path`                   | yes           | yes                  | `.`               | Project directory (the one that holds `package.json`), relative to the checkout. A path that leaves the checkout fails.       |
| `node-version`           | yes           | yes                  | `22`              | Node.js version (`22`, `22.11` or `22.11.0`).                                                                                 |
| `npm-scripts`            | yes           | yes                  | `build`           | npm scripts that must exist in `package.json`, run in order after `npm-scripts-if-present`, separated by commas or new lines. |
| `npm-scripts-if-present` | yes           | yes                  | `typecheck, lint` | npm scripts run in order after `npm ci` when `package.json` defines them, separated by commas or new lines.                   |
| `wrangler-config`        | yes           | yes                  | empty             | Wrangler configuration file, relative to `path`. Empty: `wrangler.json`, `wrangler.jsonc` or `wrangler.toml` at `path`.       |
| `wrangler-dry-run`       | yes (boolean) | yes (`true`/`false`) | `false`           | Run `wrangler deploy --dry-run` with the Wrangler of the lock file.                                                           |
| `audit-blocking-severities` | yes | yes | variable `CLOUDFLARE_WORKERS_CHECK_AUDIT_BLOCKING_SEVERITIES`, else `critical` (workflow); `critical` (action) | Severities of a dependency advisory that fail the job, separated by commas: any of `critical`, `high`, `medium`, `low`. |
| `dry-run`                | yes (boolean) | yes (`true`/`false`) | `false`           | Validate and plan only.                                                                                                       |
| `timeout-minutes`        | yes           | no                   | `20`              | Timeout of the job.                                                                                                           |

## Outputs

| Output            | Meaning                                                     |
| ----------------- | ----------------------------------------------------------- |
| `name`            | Package name read from `package.json` (empty when not set). |
| `wrangler-config` | Wrangler configuration file used, relative to `path`.       |
| `commands`        | JSON array of the planned commands, in order.               |

## Variables and secrets

No secret: a check needs no Cloudflare credentials. One variable: `CLOUDFLARE_WORKERS_CHECK_AUDIT_BLOCKING_SEVERITIES`, the default of `audit-blocking-severities` in the reusable workflow. A project whose npm scripts need a secret is out of scope for this component.

## Permissions

The caller grants `contents: read` to the calling job. The component asks for nothing more. A project with dependencies from GitHub Packages also needs `packages: read`: see "Dependencies from GitHub Packages".

## Dependencies from GitHub Packages

A project whose `package-lock.json` resolves a scope from GitHub Packages (`https://npm.pkg.github.com`) needs the job token with `packages: read`. The job of the reusable workflow declares `contents: read` only, and a caller cannot raise the permissions of a called workflow, so `npm ci` fails there with `403 Forbidden` on the first package of the scope. Run the composite action in a job of your own with `packages: read` instead. The project's `.npmrc` maps the scope to the registry and reads the token from the environment:

```ini
@example:registry=https://npm.pkg.github.com
//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}
```

```yaml
jobs:
  check:
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    permissions:
      contents: read
      # Read-only: npm ci reads the scope from GitHub Packages with the job token.
      packages: read
    steps:
      - uses: actions/checkout@<sha40> # vX.Y.Z
        with:
          persist-credentials: false
      - uses: <owner>/actions/cloudflare-workers-check@<sha40> # cloudflare-workers-check-vX.Y.Z
        env:
          NODE_AUTH_TOKEN: ${{ github.token }}
        with:
          # the inputs of the component
```

The component takes no registry input: the `env:` of the step reaches every step of the action, the npm scripts included, so the job token there must hold only `contents: read` and `packages: read`. Each package of the scope must grant the calling repository Read access (package settings, "Manage Actions access"). A GitHub App installation token is refused by that registry; the job token is the identity that works there. The reusable workflow keeps `contents: read` only, the least privilege for the projects that need no GitHub Packages scope.

## Example

Reference the component by the full commit SHA of a release tag of this component, with the tag as a comment. Find the SHA with `gh api repos/<owner>/actions/git/ref/tags/cloudflare-workers-check-vX.Y.Z` (dereference an annotated tag).

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

permissions: {}

jobs:
  cloudflare-workers-check:
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/cloudflare-workers-check.yml@<sha40> # cloudflare-workers-check-vX.Y.Z
    with:
      npm-scripts-if-present: type-check, lint
      npm-scripts: build
      wrangler-dry-run: true
```

The composite action in a job of the caller:

```yaml
steps:
  - uses: actions/checkout@<sha40> # vX.Y.Z
    with:
      persist-credentials: false
  - uses: <owner>/actions/cloudflare-workers-check@<sha40> # cloudflare-workers-check-vX.Y.Z
    with:
      path: worker
      npm-scripts: build
```
