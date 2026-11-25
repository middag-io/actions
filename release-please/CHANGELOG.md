# Changelog

## [1.4.0](https://github.com/middag-io/actions/compare/release-please-v1.3.0...release-please-v1.4.0) (2026-11-25)


### Features

* **release-please:** expose per-package releases and release pull request outputs ([da32e21](https://github.com/middag-io/actions/commit/da32e21836012cb5ba7216016bcb7eb827b789cc))

## [1.3.0](https://github.com/middag-io/actions/compare/release-please-v1.2.0...release-please-v1.3.0) (2026-11-23)


### Features

* **release-please:** drop merge-commit duplicates from the release notes ([2a5519f](https://github.com/middag-io/actions/commit/2a5519f192a17f639228f4930a76f4ca9244e938))


### Bug Fixes

* **release-please:** look up merge commits by full sha ([5d8a2c9](https://github.com/middag-io/actions/commit/5d8a2c953d8cb7858760cd1393872cf994f422a9))

### Consumer impact

* The new merge-commit dedup step runs by default and drops duplicate merge-commit entries from the release notes. It can be disabled with the dedup-merge-commits input.

## [1.2.0](https://github.com/middag-io/actions/compare/release-please-v1.1.0...release-please-v1.2.0) (2026-11-17)


### Features

* add a runner input to every reusable workflow ([9bff40f](https://github.com/middag-io/actions/commit/9bff40f1d93a2e5d673ba0edb3bc875a8d880c78))

## [1.1.0](https://github.com/middag-io/actions/compare/release-please-v1.0.0...release-please-v1.1.0) (2026-11-16)


### Features

* **release-please:** expose paths-released output ([1a9042a](https://github.com/middag-io/actions/commit/1a9042a98accec0e8abe83fd1fc7d9e511530494))
* **release-please:** expose paths-released output for monorepo packages ([08dbf84](https://github.com/middag-io/actions/commit/08dbf84809b3a48875effbed85fb875ece7e5432))

## 1.0.0 (2026-11-03)


### Features

* **release-please:** add the component ([1b821f3](https://github.com/middag-io/actions/commit/1b821f34ef0e2aeb9b57ba5790e4f6c322d81994))
* **release-please:** add the release-please component ([40edc60](https://github.com/middag-io/actions/commit/40edc6066fda0958eb02f0a538879d7660bf2fc2))


### Bug Fixes

* **release-please:** check the branch release-please reads ([aa3f57d](https://github.com/middag-io/actions/commit/aa3f57de1549fb41277544e8533d60a73075cd98))


### Miscellaneous Chores

* release 1.0.0 of each new component ([58b1982](https://github.com/middag-io/actions/commit/58b198243b0726ffd3fcc345b8e78fdcec3b5802))
