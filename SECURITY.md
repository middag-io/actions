# Security policy

## Reporting a vulnerability

Report a suspected vulnerability privately. Never use a public issue, pull request or discussion.

1. Open the **Security** tab of this repository and choose **Report a vulnerability**, or write to [security@middag.io](mailto:security@middag.io).
2. Describe the affected component and version, the impact and the steps to reproduce.
3. Do not include real credentials, tokens or personal data. Redact them.

We confirm receipt within 5 business days, and within 10 business days we tell you whether we confirm the vulnerability, how severe we consider it, and the next steps.

## Supported versions

| Version                                                                                                       | Supported                         |
| ------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| Latest release of each component                                                                              | yes                               |
| Previous major of a component, for 6 months after the next major, when a fork under a client contract uses it | security fixes only               |
| Other older releases                                                                                          | no; upgrade to the latest release |

Fixes ship as new releases. Release tags `<component>-vX.Y.Z` never move; only the major tag `<component>-vN` follows the latest stable release of its major version. The deprecation and support rules are in the section "Deprecation and support of older majors" of the [README](README.md).

## Scope

In scope: the composite actions, the reusable workflows and the scripts in this repository. Out of scope: third-party actions (report them to their maintainers), forks (report them to the fork owner) and the configuration that each caller keeps in its own repository.
