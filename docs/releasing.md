# Release process

Releases are created from an exact version tag only after the same commit has
passed the complete CI matrix and the public prepress corpus. The workflow
publishes the same checked wheel and source archive first as an immutable GitHub
Release and then to PyPI through short-lived OpenID Connect credentials. No
long-lived PyPI token is stored in GitHub.

## One-time PyPI setup

Complete both controls before pushing the first PyPI release tag:

1. Create a GitHub Actions environment named exactly `pypi`. Restrict its
   deployment branches and tags to selected tags matching `v*`, and keep it free
   of secrets and variables. A solo-maintained repository does not need an
   environment reviewer; protected `main`, restricted release tags, and the
   workflow gates remain mandatory.
2. On PyPI, configure a pending Trusted Publisher with project name `spotpdf`,
   owner `Hyperrick`, repository `spotpdf`, workflow `ci.yml`, and environment
   `pypi`. Do not omit the environment and do not add an API-token fallback.

A pending publisher does not reserve the project name. Configure it immediately
before the first release and confirm that the name is still available.

## Maintainer checklist

1. Start from a clean branch based on `main`.
2. Update the version in `pyproject.toml`, run `uv lock`, and confirm the local
   project version in `uv.lock` matches.
3. Move the release notes from `Unreleased` to a dated `X.Y.Z` section and
   update the comparison links.
4. Update all three stable install commands (`python -m pip install`, `uv`, and
   `pipx`) to the exact PyPI pin `spotpdf==X.Y.Z`.
   The pre-PyPI Git-tag channel remains valid only as a transition; never mix
   channels. Update every tag-bound `github.com` and
   `raw.githubusercontent.com` project-content URL in `README.md` to `vX.Y.Z`.
   Live contribution, support, security-policy, and release-process routes stay
   untagged.
   Also update stable prose, the example `spotpdf_version` values in `README.md`
   and `docs/json-output.md`, and both issue-template version placeholders to
   `X.Y.Z` without the tag's `v`; remove obsolete development-only release
   claims.
5. Regenerate the synthetic visuals and run every local gate:

   ```bash
   uv sync --locked --dev --group release
   uv lock --check
   uv run python scripts/check_release.py metadata --tag vX.Y.Z
   uv run python scripts/check_release.py notes --version X.Y.Z
   uv run python scripts/check_repository.py
   uv run python scripts/create_docs_images.py
   uv run python scripts/create_docs_images.py --check
   git diff --check
   uv run ruff check .
   uv run ruff format --check .
   uv run python scripts/check_python_size.py
   uv run python -m unittest discover -s tests -v
   uv run python scripts/benchmark_inventory.py --runs 9 \
     --output tmp/inventory-benchmark.json
   uv run python scripts/check_public_corpus.py
   uv build --no-build-isolation --out-dir tmp/release-dist
   uv run --no-sync python scripts/check_pypi_readme.py \
     tmp/release-dist/*.whl tmp/release-dist/*.tar.gz
   uv run --no-sync twine check --strict \
     tmp/release-dist/*.whl tmp/release-dist/*.tar.gz
   uv run python scripts/check_distribution.py tmp/release-dist
   uv run python scripts/smoke_distributions.py tmp/release-dist
   uv run python scripts/check_release.py prepare-assets \
     --version X.Y.Z --dist tmp/release-dist
   ```

6. Merge the release pull request and wait for the protected `Package` and
   `Analyze Python` checks on the exact merge commit.
7. Create and push an annotated tag on that merge commit:

   ```bash
   git switch main
   git pull --ff-only
   git tag -a vX.Y.Z -m "spotpdf X.Y.Z"
   git push origin vX.Y.Z
   ```
8. Wait for the tag workflow. Confirm that the GitHub Release became immutable
   before the `Publish PyPI` job ran, then verify the PyPI version, files, and
   publish attestations. Download the three GitHub assets, verify their checksums
   and GitHub attestations, and confirm the new Changelog comparison links.

Do not create the tag before the release commit is in `main`. The workflow
checks tag syntax, version agreement, changelog date and notes, one consistent
README install channel, PyPI-safe tag-bound README links and images,
stable-release prose, both issue-template placeholders, lockfile agreement, and
whether the tagged commit is contained in `main`.

## Automated gates

For a `v*` tag, `.github/workflows/ci.yml`:

1. runs Quality, documentation hygiene, and the Python 3.11–3.14
   Linux/macOS/Windows test matrix;
2. rejects tracked PDFs and broken local documentation file targets, regenerates every
   synthetic documentation image, requires an exact source-fingerprint manifest
   and exact SVG output, and uses bounded pixel-drift limits for PNGs so renderer
   antialiasing does not become a false release failure;
3. downloads every public corpus PDF from a commit-pinned HTTPS URL and checks
   its exact byte count and SHA-256 digest;
4. validates source and output with qpdf, checks required equal/different
   composite renders with Poppler, and checks real plate names with Ghostscript
   `tiffsep`;
5. builds with the exactly locked setuptools backend;
6. renders the packaged Markdown long description from both archives with
   PyPI's `readme-renderer[md]`, requires them to match, and rejects ambiguous
   or resource-exhausting archive metadata without extracting it; it then runs
   `twine check --strict` for metadata and warning checks, requires the exact
   canonical Support and Security project URLs in both archives, and installs
   both distributions;
7. permits exactly one wheel, one source archive, and `SHA256SUMS` in the
   GitHub artifact, while a second immutable artifact contains only the wheel
   and source archive for PyPI;
8. attests both distributions with GitHub artifact provenance;
9. extracts the curated notes from the tagged version's dated Changelog;
10. creates the GitHub Release using a job whose only write permission is
    `contents: write` and requires an immutable-release readback;
11. enters the tag-restricted `pypi` environment only after every preceding
    release job succeeds; and
12. downloads the already verified distributions in a two-step job whose only
    permission is `id-token: write`, then publishes them with PyPI's Trusted
    Publisher and automatic PyPI attestations.

The separate CodeQL workflow analyzes Python changes on pull requests and
`main`, runs weekly against current queries, and supports manual dispatch.

Release-tag runs are never cancelled by the workflow concurrency setting. Each
download boundary rechecks the exact filenames, regular-file status, and
checksums before attestation and GitHub publication. PyPI release jobs are
serialized and never rebuild a distribution. A failed production upload must
not be hidden with `skip-existing`, a stored token, or a local manual upload;
inspect PyPI state before deciding whether to rerun the failed job or publish a
new patch version.

## Verify downloaded assets

Download all three release files into an empty directory and verify them with
the checked-in release checker:

```bash
gh release download vX.Y.Z --repo Hyperrick/spotpdf --dir tmp/verify-release
python3 scripts/check_release.py verify-assets \
  --version X.Y.Z --dist tmp/verify-release
```

GitHub's `gh attestation verify` command can additionally verify that a wheel or
source archive was produced by this repository's Actions workflow.

After publication, verify the index and a clean CLI installation:

```bash
python3 -m pip index versions spotpdf
uvx --from spotpdf==X.Y.Z spotpdf --version
```

Compare PyPI's recorded file hashes with the immutable GitHub Release checksums:

```bash
curl --fail --silent --show-error \
  https://pypi.org/pypi/spotpdf/X.Y.Z/json \
  | jq --raw-output '.urls[] | "\(.digests.sha256)  \(.filename)"' \
  | sort > tmp/pypi-sha256s
sort tmp/verify-release/SHA256SUMS > tmp/github-sha256s
diff --unified tmp/github-sha256s tmp/pypi-sha256s
```

Finally, cryptographically verify the wheel's PyPI provenance against this
repository:

```bash
uvx pypi-attestations verify pypi \
  --repository https://github.com/Hyperrick/spotpdf \
  pypi:spotpdf-X.Y.Z-py3-none-any.whl
```

The PyPI project page should show both distributions and their publish
attestations. Repeat the provenance command for the source archive when a
release audit requires both files.

## Repository settings

Keep the following GitHub controls enabled:

- protected `main` with pull requests required but zero mandatory approvals for
  the solo maintainer;
- strict `Package` and `Analyze Python` checks on the current commit;
- branch protection enforced for administrators, resolved discussions and
  linear history required, and force-pushes and deletions disabled;
- read-only default Actions token permissions;
- private vulnerability reporting and Dependabot;
- restricted creation, update, and deletion of `v*` tags, with only the
  repository owner allowed to bypass that ruleset;
- immutable releases, so published tags and assets cannot be replaced;
- the tag-restricted, secret-free `pypi` environment; and
- the exact PyPI Trusted Publisher binding to `ci.yml` plus that environment.

The workflow is fail-closed if these settings or required permissions prevent a
release. It never falls back to an unverified upload path.
