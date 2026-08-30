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
4. Update both stable install commands in `README.md` to `@vX.Y.Z`.
5. Regenerate the synthetic visuals and run every local gate:

   ```bash
   uv sync --locked --dev
   uv run python scripts/create_docs_images.py
   uv run ruff check .
   uv run ruff format --check .
   uv run python -m unittest discover -s tests -v
   uv run python scripts/check_public_corpus.py
   uv build --no-build-isolation --out-dir tmp/release-dist
   uv run python scripts/check_distribution.py tmp/release-dist
   uv run python scripts/smoke_distributions.py tmp/release-dist
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

Do not create the tag before the release commit is in `main`. The workflow
checks tag syntax, version agreement, changelog date, README install links,
lockfile agreement, and whether the tagged commit is contained in `main`.

## Automated gates

For a `v*` tag, `.github/workflows/ci.yml`:

1. runs Quality and the Python 3.11–3.14 Linux/macOS/Windows test matrix;
2. downloads every public corpus PDF from a commit-pinned HTTPS URL and checks
   its exact byte count and SHA-256 digest;
3. validates source and output with qpdf, checks required equal/different
   composite renders with Poppler, and checks real plate names with Ghostscript
   `tiffsep`;
4. builds with the exactly locked setuptools backend;
5. inspects and installs both the wheel and source archive;
6. permits exactly one wheel, one source archive, and `SHA256SUMS`;
7. attests both distributions with GitHub artifact provenance; and
8. creates the GitHub Release using a job whose only write permission is
   `contents: write`.

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
