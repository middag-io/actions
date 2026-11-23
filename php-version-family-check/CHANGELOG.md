# Changelog

## [1.3.0](https://github.com/middag-io/actions/compare/php-version-family-check-v1.2.0...php-version-family-check-v1.3.0) (2026-11-23)


### Features

* **php-version-family-check:** add opt-in per-lib minor windows and freeze minor windows on prereleases ([beeedd1](https://github.com/middag-io/actions/commit/beeedd19184cff7d56b4017705ea4c33928eb396))

## [1.2.0](https://github.com/middag-io/actions/compare/php-version-family-check-v1.1.0...php-version-family-check-v1.2.0) (2026-11-23)


### Features

* **php-version-family-check:** accept Release-As release candidates of the window ([a436d15](https://github.com/middag-io/actions/commit/a436d15db2ff841a50848f910c793b5df8ae4715))


### Bug Fixes

* **php-version-family-check:** reject trailing whitespace in rc Release-As ([6762fee](https://github.com/middag-io/actions/commit/6762fee0a8694a4b452145732fd19c76a1d86f4f))

### Consumer impact

* Additive: release candidates of the window set with Release-As are now accepted, and trailing whitespace in an rc Release-As is rejected. Existing callers are unaffected by default.

## [1.1.0](https://github.com/middag-io/actions/compare/php-version-family-check-v1.0.0...php-version-family-check-v1.1.0) (2026-11-17)


### Features

* add a runner input to every reusable workflow ([9bff40f](https://github.com/middag-io/actions/commit/9bff40f1d93a2e5d673ba0edb3bc875a8d880c78))

## 1.0.0 (2026-11-05)


### Features

* **php-version-family-check:** add a pull request check of a version family window ([7ad8c82](https://github.com/middag-io/actions/commit/7ad8c822026396cfa2e6307e6a54c07c5e570819))
* **php-version-family-check:** add a pull request check of a version family window ([c8feda0](https://github.com/middag-io/actions/commit/c8feda036738f9edc7c7e8f4046ed27cdafc184a))
* **php-version-family-check:** gate Release-As, stale windows and other base branches ([6dd1f7d](https://github.com/middag-io/actions/commit/6dd1f7dfaee505742edbb64ebc9a84321f7fc475))


### Documentation

* **php-version-family-check:** document the window rules, the status context and re-runs ([5b92c54](https://github.com/middag-io/actions/commit/5b92c545e6da92fa8cc10c651a20ff647d0c473a))


### Miscellaneous Chores

* release 1.0.0 of each new component ([58b1982](https://github.com/middag-io/actions/commit/58b198243b0726ffd3fcc345b8e78fdcec3b5802))
