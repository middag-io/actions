# cloudflare-pages-deploy

Builds a static site and deploys it to Cloudflare Pages, only when the site sources changed since the last deploy. The defaults fit a [VitePress](https://vitepress.dev/) site; any site built by an npm script works.

Two forms, one name:

- reusable workflow `.github/workflows/cloudflare-pages-deploy.yml`: checks out the calling repository and runs the composite action in its own job, with a timeout and a concurrency group;
- composite action `cloudflare-pages-deploy/action.yml`: runs in a job of the caller, after the caller's own checkout.

## What it does

1. Validates the inputs. A missing project name, account id or API token fails here, before any other step, and the error names the input and the variable.
2. Computes the content hash of the site sources: SHA-256 over the path and the SHA-256 of every file under `docs-path`, except `node_modules/`, `.git/`, `.vitepress/dist/`, `.vitepress/cache/` and `dist-path`.
3. Reads `<deploy-url>/.content-hash`, the hash that the last deploy wrote, with the user agent `cloudflare-pages-deploy`. When it equals the local hash, the run ends without building or deploying. When the file is missing or unreadable, the site is deployed.
4. Sets up Node.js, runs `npm ci` and the build command in `build-working-directory`.
5. Writes the content hash to `<dist-path>/.content-hash`.
6. Deploys `dist-path` with `wrangler pages deploy <dist-path> --project-name=<project-name>` ([cloudflare/wrangler-action](https://github.com/cloudflare/wrangler-action)). The command passes no `--branch`: Wrangler decides the branch and with it whether the deployment is production or preview (see the [Wrangler documentation](https://developers.cloudflare.com/workers/wrangler/commands/pages/)). Call the component from the production branch of the project to update the production site.

With `dry-run: true`, it runs steps 1 and 2 only and reports the plan: no credentials, no fetch, no build and no deploy.

## What it deletes

Nothing in the repository. A deploy replaces the content served by the Pages environment of the branch: a file that is not in the new `dist-path` is no longer served there. Earlier deployments stay in the project and can be rolled back from Cloudflare.

The component never creates the Pages project and never touches its custom domains or DNS records: manage them outside the component.

## Inputs

| Input                     | Required                  | Default                                                    | Description                                                                                                                            |
| ------------------------- | ------------------------- | ---------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `docs-path`               | yes                       | —                                                          | Directory of the site sources, relative to the repository root (for example `docs` or `.`).                                            |
| `project-name`            | yes                       | variable `CLOUDFLARE_PAGES_DEPLOY_PROJECT_NAME` (workflow) | Cloudflare Pages project name. The component accepts 1 to 58 lowercase letters, digits and dashes, not starting or ending with a dash. |
| `deploy-url`              | no                        | `https://<project-name>.pages.dev`                         | URL of the deployed site, where the last deploy wrote `.content-hash`. Use the custom domain when `pages.dev` is not reachable.        |
| `account-id`              | unless `dry-run`          | variable `CLOUDFLARE_PAGES_DEPLOY_ACCOUNT_ID` (workflow)   | Cloudflare account id (32 hexadecimal characters). The workflow also takes it as the secret `account-id`, which wins over the input.   |
| `node-version`            | no                        | `22`                                                       | Node.js version of the build.                                                                                                          |
| `build-command`           | no                        | `npm run docs:build`                                       | Build command, run after `npm ci` in `build-working-directory`. It runs as written: it comes from the caller.                          |
| `build-working-directory` | no                        | `docs-path`                                                | Directory where `npm ci` and the build command run.                                                                                    |
| `dist-path`               | no                        | `<docs-path>/.vitepress/dist`                              | Directory of the built site, relative to the repository root.                                                                          |
| `cache-dependency-path`   | no                        | empty (no cache)                                           | Lockfile that keys the npm cache of `actions/setup-node`.                                                                              |
| `force`                   | no                        | `false`                                                    | Deploy even when the content hash matches the deployed one.                                                                            |
| `dry-run`                 | no                        | `false`                                                    | Validate the inputs and compute the content hash only.                                                                                 |
| `timeout-minutes`         | no (workflow only)        | `20`                                                       | Timeout of the job.                                                                                                                    |
| `api-token`               | unless `dry-run` (action) | —                                                          | In the composite action only: the API token, passed from a secret. The workflow takes it as the secret `api-token`.                    |

Paths accept only `[A-Za-z0-9._/-]`, must be relative and must not contain `..`.

In the composite action, the variable defaults do not apply: pass every value through `with:`.

## Outputs

| Output           | Description                                                                                |
| ---------------- | ------------------------------------------------------------------------------------------ |
| `content-hash`   | SHA-256 content hash of the site sources.                                                  |
| `deploy`         | `true` when this run built and deployed the site, `false` otherwise (always in a dry run). |
| `dist-path`      | Directory of the built site that is (or would be) deployed.                                |
| `command`        | Wrangler command of the deploy.                                                            |
| `deployment-url` | URL of the new Cloudflare Pages deployment; empty when nothing was deployed.               |

## Variables and secrets

| Name                                   | Kind     | Used for                                                    |
| -------------------------------------- | -------- | ----------------------------------------------------------- |
| `CLOUDFLARE_PAGES_DEPLOY_PROJECT_NAME` | variable | default of `project-name` (workflow only)                   |
| `CLOUDFLARE_PAGES_DEPLOY_ACCOUNT_ID`   | secret   | value the caller passes as `account-id` (public repository) |
| `CLOUDFLARE_PAGES_DEPLOY_ACCOUNT_ID`   | variable | default of `account-id` (workflow only; private repository) |
| `CLOUDFLARE_PAGES_DEPLOY_API_TOKEN`    | secret   | value the caller passes as `api-token`                      |

For `project-name`, a repository variable wins over an organization variable with the same name, and an explicit input wins over both. For `account-id`, the secret `account-id` of the workflow wins over the input and its variable default. The API token needs the permission to edit Cloudflare Pages in the account; it is never inherited.

**Account id in a public repository: pass it as a secret.** The logs of a public repository are public. GitHub masks a value that comes from a secret in every log line. A value that comes from an input or a variable shows in the log of the step that receives it: the caller's step and the first step of the composite action. The action masks it (`::add-mask::`) from that first step on, which hides it in the Wrangler step and its output, not in those first lines. In a private repository, the variable is enough.

## Permissions

The job needs `contents: read` only, to check out the site sources. The deploy uses the Cloudflare API token, not the GitHub token. The build runs `npm ci` without a registry token: a site that installs private packages is not supported.

## Example

Reference the component by its major tag `cloudflare-pages-deploy-v1`. A fork and a caller outside the maintainer organization pin the full commit SHA of a release tag instead, with the tag as a comment (`@<sha40> # cloudflare-pages-deploy-vX.Y.Z`). Find the SHA with `gh api repos/<owner>/actions/git/ref/tags/cloudflare-pages-deploy-vX.Y.Z` (dereference an annotated tag).

Reusable workflow, after a release:

```yaml
jobs:
  docs:
    needs: release-please
    if: needs.release-please.outputs.release_created == 'true'
    permissions:
      contents: read
    uses: <owner>/actions/.github/workflows/cloudflare-pages-deploy.yml@cloudflare-pages-deploy-v1
    with:
      docs-path: docs-site
      project-name: example-docs
    secrets:
      account-id: ${{ secrets.CLOUDFLARE_PAGES_DEPLOY_ACCOUNT_ID }}
      api-token: ${{ secrets.CLOUDFLARE_PAGES_DEPLOY_API_TOKEN }}
```

Composite action, in a job of the caller:

```yaml
steps:
  - uses: actions/checkout@<sha40> # vX.Y.Z
    with:
      persist-credentials: false
  - uses: <owner>/actions/cloudflare-pages-deploy@cloudflare-pages-deploy-v1
    with:
      docs-path: docs
      project-name: example-docs
      account-id: ${{ secrets.CLOUDFLARE_PAGES_DEPLOY_ACCOUNT_ID }}
      api-token: ${{ secrets.CLOUDFLARE_PAGES_DEPLOY_API_TOKEN }}
```

Filter the trigger in the caller (for example `paths:` on the site sources): the reusable workflow does not filter.
