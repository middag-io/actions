# Security policy

## Reporting a vulnerability

Report a suspected vulnerability privately. Never use a public issue, pull request or discussion.

1. Open the **Security** tab of this repository and choose **Report a vulnerability**, or write to [security@middag.io](mailto:security@middag.io).
2. Describe the affected component and version, the impact and the steps to reproduce.
3. Do not include real credentials, tokens or personal data. Redact them.

We confirm receipt within 5 business days, and within 10 business days we tell you whether we confirm the vulnerability, how severe we consider it, and the next steps.

## Supported versions

| Version                                                       | Supported                         |
| ------------------------------------------------------------- | --------------------------------- |
| Latest release of each component, release candidates included | yes                               |
| Older releases                                                | no; upgrade to the latest release |

No component has been released yet. Fixes ship as new releases. Release tags `<component>-vX.Y.Z` never move; only the major tag `<component>-vN` follows the latest release of its major version.

## Scope

In scope: the composite actions, the reusable workflows and the scripts in this repository. Out of scope: third-party actions (report them to their maintainers), forks (report them to the fork owner) and the configuration that each caller keeps in its own repository.
