# Release process

Releases are created from an exact version tag only after the same commit has
passed the complete CI matrix and the public prepress corpus. The workflow does
not publish to PyPI; release installation uses the versioned Git tag or attached
GitHub assets.

## Maintainer checklist

1. Start from a clean branch based on `main`.
2. Update the version in `pyproject.toml`, run `uv lock`, and confirm the local
   project version in `uv.lock` matches.
3. Move the release notes from `Unreleased` to a dated `X.Y.Z` section and
   update the comparison links.
4. Update both stable install commands to the `vX.Y.Z` Git tag. Update stable
   prose, the example `spotpdf_version` values in `README.md` and
   `docs/json-output.md`, and the bug-report version placeholder to `X.Y.Z`
   without the tag's `v`; remove obsolete development-only release claims.
5. Regenerate the synthetic visuals and run every local gate:

   ```bash
   uv sync --locked --dev
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
   uv run python scripts/check_distribution.py tmp/release-dist
   uv run python scripts/smoke_distributions.py tmp/release-dist
   uv run python scripts/check_release.py prepare-assets \
     --version X.Y.Z --dist tmp/release-dist
   ```

6. Merge the release pull request and wait for the protected `Package` check on
   the exact merge commit.
7. Create and push an annotated tag on that merge commit:

   ```bash
   git switch main
   git pull --ff-only
   git tag -a vX.Y.Z -m "spotpdf X.Y.Z"
   git push origin vX.Y.Z
   ```
8. Wait for the tag workflow, download the three immutable release assets,
   verify their checksums and attestations, and confirm the new Changelog
   comparison links resolve after the tag exists.

Do not create the tag before the release commit is in `main`. The workflow
checks tag syntax, version agreement, changelog date and notes, README install
links and stable-release prose, the bug-report placeholder, lockfile agreement,
and whether the tagged commit is contained in `main`.

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
6. inspects and installs both the wheel and source archive;
7. permits exactly one wheel, one source archive, and `SHA256SUMS`;
8. attests both distributions with GitHub artifact provenance;
9. extracts the curated notes from the tagged version's dated Changelog
   section; and
10. creates the GitHub Release using a job whose only write permission is
   `contents: write`.

The separate CodeQL workflow analyzes Python changes on pull requests and
`main`, runs weekly against current queries, and supports manual dispatch.

Release-tag runs are never cancelled by the workflow concurrency setting. Each
download boundary rechecks the exact filenames, regular-file status, and
checksums before attestation and publication.

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

## Repository settings

Keep the following GitHub controls enabled:

- protected `main` with the `Package` status check required;
- read-only default Actions token permissions;
- private vulnerability reporting and Dependabot;
- restricted creation/deletion of `v*` tags; and
- immutable releases, so published tags and assets cannot be replaced.

The workflow is fail-closed if these settings or required permissions prevent a
release. It never falls back to an unverified upload path.
