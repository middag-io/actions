# Runbook: cutting a family MAJOR as release candidates

How a family of libraries (packages that share the MAJOR, each in its own repository, each released by release-please) cuts its next MAJOR as release candidates, freezes it, and promotes it to the final version, using [`php-version-family-check`](README.md). The example is the family MAJOR `2.0`; replace it with your own.

The procedure never opens the window on the whole organization. It opens it per repository.

## Terms

- **Window variable**: the caller variable that feeds the `window` input. Here `PHP_LIBS_OPEN_RELEASE`; use your own name.
- **Cut PR**: the one pull request per library that moves the library to its first candidate.
- **Layers**: libraries ordered by their dependencies on one another. A library with no sibling dependency is layer 0, a library that requires only layer 0 is layer 1, and so on. Libraries of the same layer are independent and can be cut in any order or in parallel. Cut a layer only after every library it requires is tagged and on Packagist.
- **Sibling**: another library of the same family that a library requires in its `composer.json`.

## Rules that hold throughout

1. Set the window variable **per repository**, only on the repositories of the family. Never set it on the organization: repositories outside the family read the same variable name and would open a window they do not own. After the 2.0 promotion the organization variable must not exist at all: a repository variable shadows it, but a leftover organization value opens a window on every repository without a variable of its own.
2. Sibling constraints are `^2.0.0-rc.1`, never a dual range such as `^1.x || ^2.x`. Stability flags (`minimum-stability`, `prefer-stable`) count only in the root `composer.json` of the project that installs the packages, never in a dependency. Verify that the roots of the family are `minimum-stability: dev` with `prefer-stable: true` (or otherwise allow release candidates); only then does a constraint that names `-rc.1` resolve without further change.
3. Merge only with a merge commit, so that the `Release-As:` footer reaches the base branch as written. Update a branch by merging the base branch into it, never by rebasing or force-pushing.
4. Never edit `CHANGELOG.md`, the manifest or a tag by hand. release-please writes them.
5. A lock refresh (`composer update <vendor>/<sibling>`) happens only after the sibling's tag is visible on Packagist, and its commit is `chore(deps): ...`.

## Preconditions

Check each one before the first cut PR. Stop if one fails.

- No open release pull request in any library of the family. Changing the release-please configuration (below) rewrites an open release pull request to a `1.x` candidate version. Merge it or close it first.
- The `php-version-family-check` and `github-release-major-check` versions that understand `Release-As: X.Y.0-rc.N` are released, and every library of the family calls those versions or later.
- Repositories that depend on the family but are not part of it (for example tooling or products that require the libraries) have widened their constraints so that they accept `2.0.0-rc.N` and `2.x`.
- The default branch of every library is frozen: no pull request merges while the cut is in progress, except the cut PRs.
- The family's checks are green on the default branch of every library.

## Cut, library by library

Repeat for each library, layer 0 first. Do one library completely (steps 1 to 8) before the next library of the next layer starts.

1. **Open the window on this repository.** Set `PHP_LIBS_OPEN_RELEASE=2.0` as a variable of this repository only.
2. **Open the cut PR** from a branch off the default branch. It carries these changes:
   - `release-please-config.json`, on the package: `"versioning": "prerelease"`, `"prerelease-type": "rc"` and `"prerelease": true`.
   - The family workflows of the library (the calls to `php-version-family-check`): `family-from: 2.0.0-rc.1`.
   - `composer.json`: each sibling requirement becomes `^2.0.0-rc.1`. Layer 0 has no sibling, so it changes nothing here.
   - One commit whose message ends with the footer `Release-As: 2.0.0-rc.1`. An empty commit is enough:

     ```text
     chore: cut the first 2.0 release candidate

     Release-As: 2.0.0-rc.1
     ```
3. **Merge the cut PR** (merge commit) once its checks are green. The family check passes the footer only because the window is open and `2.0.0` is the first version of the window.
4. **Run the release workflow** of the repository (`release.yml`). release-please opens the release pull request for `2.0.0-rc.1`.
5. **Merge the release pull request**, then verify:
   - the tag `v2.0.0-rc.1` exists;
   - the GitHub release exists and is flagged **prerelease**;
   - Packagist lists `2.0.0-rc.1`.
6. **Clear the window variable of this repository.** This is the freeze: from now on a `feat` or a breaking change fails the family check (the manifest is at `2.0.0-rc.1`, which is at least `family-from`), and only a `fix` passes. A `fix` bumps only the candidate counter: `2.0.0-rc.2`.
7. **Refresh the locks of the dependants.** When the next layer's libraries have a `composer.lock`, run `composer update <vendor>/<this-library>` there, only now that the tag is on Packagist, and commit it as `chore(deps): ...`.
8. **Record the result** (tag, release flag, Packagist) before starting the next library.

If a candidate has a defect, fix it with a `fix:` pull request on the library, release `2.0.0-rc.2`, and refresh the dependants again. Do not reopen the window to fix a defect.

## Promotion to the final version

Do this when every candidate is accepted.

1. **Reopen the window** (`PHP_LIBS_OPEN_RELEASE=2.0`) on each repository of the family, because a final version cannot be named while the window is closed.
2. **Per library, one PR**, layer 0 first, each after the layers it requires are final and on Packagist:
   - commit footer `Release-As: 2.0.0`;
   - revert the three release-please keys (`versioning`, `prerelease-type`, `prerelease`) to what they were before the cut;
   - sibling constraints back to `^2.0`;
   - `family-from: 2.0.0`, and `per-lib-from: 2.0.0` next to it (the opt-in of "Per-lib minor"). Set `per-lib-from` only here, in the promotion PR of the library, never in a cut PR: during the candidates the family freeze applies.
3. **Merge, run the release workflow, merge the release pull request.** Verify the tag `v2.0.0`, that the GitHub release is **not** flagged prerelease, and that Packagist lists `2.0.0`.
4. **Delete the window variable** of the repository. With the manifest at `2.0.0`, the `2.0` window is no longer ahead and counts as closed in any case; deleting the variable keeps the configuration honest.
5. **After the promotion**, a `feat` needs the per-repository window with the next minor of that library (a library at `2.0.x` takes `2.1`, a library at `2.3.x` takes `2.4`), and a breaking change needs the next family MAJOR window (`3.0`). Confirm that the organization variable does not exist.

## Per-lib minor

From `per-lib-from` on, the libraries share only the MAJOR: each one cuts its own minor, when it has a `feat` to ship.

- The product owner opens the window on one repository: `gh variable set PHP_LIBS_OPEN_RELEASE --repo <owner>/<library> --body 2.4`, where `2.4` is the minor of that library plus one. Other values (`2.6` on a library at `2.3.x`) leave the window closed, and the check says why.
- A `Release-As` footer is refused in a per-lib window: release-please derives the minor from the `feat` commits.
- The release closes the window by itself (`2.4.0` in the manifest). Delete the repository variable afterwards.
- A breaking change still waits for a family MAJOR window (`3.0`), set per repository, layer by layer, as in the cut above.
- A library that needs a new API of a sibling raises its constraint to `^2.N` after the sibling's tag is on Packagist.

## Side effects of a release candidate

- Verify that your image publishing skips the `latest` tag for candidate tags: a candidate should publish only its own tag and never move `latest`.
- Verify whether any mirror that copies the contracts of the libraries is triggered by candidate tags. Decide whether it should run on the candidate tag or only on the final tag.
- Consumers that do not allow prereleases never resolve a candidate: they stay on the last `1.x` until the final version.

## Rehearsal

Rehearse the whole procedure before the real cut, on throwaway repositories (or forks) that mirror the family: the same layer order, release-please configuration and workflows, with a throwaway Packagist vendor or a private Composer repository in place of Packagist.

1. Run the cut for every layer, with `Release-As: 2.0.0-rc.1`.
2. Confirm for each library: the tag, the prerelease flag, the package listing, the freeze (a `feat` pull request fails, a `fix` pull request passes and bumps `rc.2`).
3. Promote with `Release-As: 2.0.0` and confirm the release is not flagged prerelease.
4. Confirm that a pull request with `Release-As: 3.0.0-rc.1` in window `2.0`, a `rc.0` footer and a `beta` footer all fail.
5. With `per-lib-from: 2.0.0` set, open a per-lib minor on one throwaway repository (`--body 2.1`), cut `2.1.0` with a `feat`, and check that the window closes by itself and that `Release-As` and a window of another minor (`2.5`) are refused.
6. Delete the throwaway repositories and variables.

## Rollback

Until a tag is pushed, close the cut PR and restore the three release-please keys and `family-from`. After a tag is pushed, never delete it and never reuse its number: release the next candidate with a `fix:` pull request.
