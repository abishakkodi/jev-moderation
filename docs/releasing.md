# Releasing the SDKs

The source repository is public. Registry packages are **not yet published**.
The first release remains blocked on a license choice and registry-owner setup.
Registry checks on 2026-09-23 returned 404 for `jev-moderation` on npm and PyPI;
this does not reserve the names or guarantee that the registries will accept them.

## One-time account setup

1. Choose the repository license. Set the same SPDX license string in both package
   manifests, add `LICENSE` at the root, then run `python3 scripts/sync_shared.py`.
2. Log in to your npm account locally (`npm login`). For the initial package,
   publish the tested tarball manually if package settings are not yet available:
   `npm publish dist/jev-moderation-0.1.0.tgz --access public --ignore-scripts`.
   This is a real, immutable registry release; use the exact tarball tested below.
3. In npm's package settings, add a GitHub trusted publisher:
   owner `abishakkodi`, repository `jev-moderation`, workflow `release.yml`,
   environment `npm`. Enable direct `npm publish` for this publisher.
4. On PyPI, add a **pending publisher** for the new project `jev-moderation`:
   owner `abishakkodi`, repository `jev-moderation`, workflow `release.yml`,
   environment `pypi`. A pending publisher lets OIDC create the project on first publish.
5. Create GitHub environments named `npm` and `pypi`. If desired, restrict them to
   release tags and add your approval policy. Never commit tokens or paste them in chat.

References: [npm trusted publishing](https://docs.npmjs.com/trusted-publishers/),
[PyPI pending publishers](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).

## Validate an early release

```sh
npm ci
python3 -m venv .venv
.venv/bin/pip install -e 'packages/python[dev]'
python3 scripts/sync_shared.py --check
npm test
npm run test:examples
.venv/bin/pytest packages/python/tests tests examples/test_custom_filter.py
npm run test:parity
.venv/bin/python scripts/check_release.py --tag v0.1.0
mkdir -p dist
npm pack -w jev-moderation --pack-destination dist
.venv/bin/python -m build packages/python --outdir dist
.venv/bin/python -m twine check --strict dist/*.whl dist/*.tar.gz
.venv/bin/python scripts/smoke_install.py --artifacts dist
```

The consumer check creates temporary projects **outside the repository**, installs
the actual package files and their dependencies, type-checks a TypeScript consumer,
exercises Python sync/async imports, tests a custom policy, and invokes both CLIs.
It needs registry access for public dependencies, but no TypeSafe key or inference.

Update both package versions and the changelog together. Commit and push the tested
source before tagging it:

```sh
git tag v0.1.0
git push origin v0.1.0
```

Tagging triggers `release.yml`: the CI matrix must pass, release versions/licenses
must match, packages are built and tested, then npm/PyPI publish **the tested files**
using OIDC. The final jobs install the exact versions from the real registries and
create a GitHub prerelease with downloadable artifacts. A passing local build is
not proof that account trust is configured correctly.

For first-time npm bootstrap, prefer using the `release-distributions` artifact
from that workflow's successful build job for the manual publish, then configure
npm trust and rerun **failed jobs only**. This preserves the tested bytes. The
workflow can skip an existing version only if registry file hashes match exactly.
If one registry succeeds and the other fails, rerun failed jobs against those same
artifacts. Do not rebuild or change an already-published version; resolve the
collision or release a new version. Partial PyPI uploads need inspection.

Early releases are marked as GitHub prereleases and PyPI development status Alpha.
Version `0.1.0` is still a normal installable registry version, not a SemVer
prerelease. No live accuracy guarantee is made until the evaluation process below
has been run and its results reviewed.

## Measure moderation quality

Add `TYPESAFE_API_KEY` as a GitHub Actions repository secret or set it in your local
shell. Never commit a key. The **Live Jev evaluation (billable)** workflow runs only
when manually dispatched. It uses the policy committed at the selected ref and
uploads its report even if quality gates fail.

```sh
.venv/bin/python scripts/live_eval.py --split tuning
# Edit thresholds or definitions and evaluate a versioned candidate on tuning.
.venv/bin/python scripts/live_eval.py --split tuning --policy candidate.json
# Freeze the candidate before consulting this split:
.venv/bin/python scripts/live_eval.py --split held-out --policy candidate.json
```

The two bundled splits are small, synthetic starter sets (14 tuning / 16 held-out
cases). They cover all default categories plus quotation, help-seeking, and prompt
injection. Add representative labeled data before drawing production conclusions.
The starter gates require action accuracy ≥ 0.85 and review rate ≤ 0.25. They are
explicit engineering targets, not observed results or promised performance.

Use the SDK's `rethreshold` helper to sweep thresholds against a tuning report
without new inference. Definition changes require fresh calls. The tooling never
silently rewrites a default policy or tunes against held-out examples. Preserve
policy hashes and model versions with reports when deciding whether to ship a
calibrated preset revision.

## Consumer installation after publication

```sh
npm install jev-moderation
pip install jev-moderation
```

Imports remain `import { Moderator } from "jev-moderation"` and
`from jev_moderation import Moderator`. Developers provide their own TypeSafe key;
installing the SDK does not grant API access. Both packages include the
`jev-moderation` CLI. npm is ESM-only; Python supports 3.10+ and Node supports 20.19+.
