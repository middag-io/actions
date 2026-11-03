# cloudflare-workers-deploy

Builds a Node.js project and deploys its Cloudflare Worker with the project's own Wrangler. Each run:

1. validates every input and the project directory, and prints the plan (no credential, no network);
2. sets up Node.js and installs the dependencies with `npm ci` (no dependency cache);
3. runs one `package.json` script as the build (`npm run <build-script>`), without the API token;
4. runs `wrangler deploy` from the project's dependencies (`npm exec --no -- wrangler deploy --config <file> [--env <name>] [--name <worker>]`), with the API token.

A dry run stops after step 1: no credentials, no install, no build, no deployment.

The component runs no free shell command. The build is a named script of the project's `package.json`; the deployment is always `wrangler deploy` with the inputs below.

Two forms, one name:

| Form | Path | Use it when |
| --- | --- | --- |
| Reusable workflow | `.github/workflows/cloudflare-workers-deploy.yml` | the default: one job with checkout, minimum permissions, concurrency, the GitHub environment and the variable fallback |
| Composite action | `cloudflare-workers-deploy/action.yml` | the project is already checked out in a job of the caller, or the token comes from a secret manager in the same job |

## Project requirements

- `package.json` and `package-lock.json` in `working-directory` (the install is `npm ci`).
- `wrangler` in the `dependencies` or `devDependencies` of that `package.json`. The component never downloads another Wrangler.
- A Wrangler configuration file in `working-directory`: the input `wrangler-config`, or else the first of `wrangler.jsonc`, `wrangler.json` and `wrangler.toml`.
- When the Worker needs a build step (bundler, framework adapter), a `package.json` script that writes what the configuration's `main` points to. Pass its name in `build-script`; an empty `build-script` skips the build.

## What it replaces and deletes

- **Replaces the deployed version of the Worker** named by the configuration (or by `worker-name`) in the selected Wrangler environment. The previous versions stay in the Worker's version history.
- **Applies the Wrangler configuration** as `wrangler deploy` does. Variables changed in the Cloudflare dashboard are overridden by the next deployment; the component does not pass `--keep-vars`. Secrets of the Worker are never deleted by a deployment ([Wrangler commands, `deploy`](https://developers.cloudflare.com/workers/wrangler/commands/workers/)).
- **Deletes nothing** in the repository, in the GitHub environment or in other Workers.

## Values: input, repository variable, organization variable

Each value comes from the most specific level that sets it:

1. an explicit input in the caller's `with:`;
2. a variable of the calling repository;
3. a variable of the calling organization.

| Input | Variable | Without input and variable |
| --- | --- | --- |
| `account-id` | `CLOUDFLARE_WORKERS_DEPLOY_ACCOUNT_ID` | Wrangler takes the account of the configuration (`account_id`) |

The secret is stored as `CLOUDFLARE_WORKERS_DEPLOY_API_TOKEN` and passed with one explicit line (never `secrets: inherit`). Use an API token allowed to edit Workers scripts on the target account, and nothing more.

The composite action reads no variable: it receives every value through `with:`.

## Inputs

"Both" means the reusable workflow and the composite action.

| Input | Form | Required | Default | Rule |
| --- | --- | --- | --- | --- |
| `working-directory` | both | no | `.` | relative to the repository root; never absolute, never `..`, never a link out of the workspace |
| `node-version` | both | no | `22` | `<major>[.<minor>[.<patch>]]` or `lts/<name>` |
| `build-script` | both | no | `build` | a script name of `package.json` (`^[A-Za-z0-9][A-Za-z0-9:._-]{0,99}$`); empty skips the build |
| `wrangler-config` | both | no | detected | `.jsonc`, `.json` or `.toml`, relative to `working-directory`, inside it |
| `wrangler-environment` | both | no | empty | `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`; passed as `--env` |
| `worker-name` | both | no | empty | `^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$`; passed as `--name` |
| `account-id` | both | no | variable (reusable) | 32 lowercase hexadecimal characters; set as `CLOUDFLARE_ACCOUNT_ID` for Wrangler |
| `github-environment` | reusable | no | `production` | GitHub environment of the deployment job (protection rules and deployment history); not used in a dry run |
| `dry-run` | both | no | `false` | validate and print the plan only |
| `timeout-minutes` | reusable | no | `20` | job timeout |
| `api-token` | composite | unless dry run | empty | pass from a secret |

## Secrets (reusable workflow)

| Secret | Required | Content |
| --- | --- | --- |
| `api-token` | unless `dry-run` | Cloudflare API token; Wrangler reads it as `CLOUDFLARE_API_TOKEN` |

## Outputs

| Output | Content |
| --- | --- |
| `wrangler-config` | the Wrangler configuration file used, relative to `working-directory` |
| `version-id` | the id of the Worker version created, read from the Wrangler output (`Current Version ID:`); empty in a dry run, and empty with a warning when the output names none |

## Permissions

The job needs `contents: read` only: the checkout uses the GitHub token, and the deployment uses the Cloudflare token.

## Calling the reusable workflow

```yaml
on:
  push:
    branches: [main]
  workflow_dispatch:

permissions: {}

jobs:
  deploy:
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/cloudflare-workers-deploy.yml@<sha40> # cloudflare-workers-deploy-vX.Y.Z
    with:
      build-script: build:worker
      wrangler-environment: production
      github-environment: production
    secrets:
      api-token: ${{ secrets.CLOUDFLARE_WORKERS_DEPLOY_API_TOKEN }}
```

## Calling the composite action

```yaml
jobs:
  deploy:
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    permissions:
      contents: read
    environment: production
    concurrency:
      group: worker-deploy-${{ github.repository }}-production
      cancel-in-progress: false
    steps:
      - uses: actions/checkout@<sha40> # vX.Y.Z
        with:
          persist-credentials: false
      - uses: <owner>/actions/cloudflare-workers-deploy@<sha40> # cloudflare-workers-deploy-vX.Y.Z
        with:
          build-script: build:worker
          wrangler-environment: production
          api-token: ${{ secrets.CLOUDFLARE_WORKERS_DEPLOY_API_TOKEN }}
```

## Implementation notes

- The API token reaches only the deployment step. The install and the build run the project's code without it.
- No dependency cache: a deployment installs from the lockfile only, so that a cache written by another workflow never reaches a running Worker.
- `wrangler deploy` runs through `npm exec --no`, which matches `wrangler` with the version installed in the project from the lockfile. The plan step refuses a project that does not declare `wrangler`.
- One deployment queue per repository, working directory, configuration, Wrangler environment and Worker name; a queued deployment is never cancelled.
