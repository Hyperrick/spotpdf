# Contributing to spotpdf

Thank you for helping make PDF spot-color processing safer and easier to audit.

## Before opening an issue

- Search existing issues.
- Use the route described in [SUPPORT.md](SUPPORT.md) for usage questions, bugs,
  feature requests, and private security reports.
- Include the `spotpdf`, Python, and operating-system versions.
- Include the exact command, stdout, stderr, expected result, and actual result.
- Say whether the PDF is signed, encrypted, or modification-restricted.
- Prefer a minimal PDF generated from code.

Never upload a confidential customer or production PDF to a public issue. If a
reproducer cannot be shared safely, describe its structure or create a synthetic
equivalent.

Security vulnerabilities belong in a private report as described in
[SECURITY.md](SECURITY.md), not a public issue.

## Development setup

```bash
git clone https://github.com/Hyperrick/spotpdf.git
cd spotpdf
uv sync --locked --dev
uv run python -m unittest discover -s tests -v
```

Before submitting a pull request, run:

```bash
uv sync --locked --dev --group release
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run python scripts/check_python_size.py
uv run python scripts/check_repository.py
uv run python scripts/create_docs_images.py
uv run python scripts/create_docs_images.py --check
uv run python -m unittest discover -s tests -v
uv run python scripts/benchmark_inventory.py --runs 3
artifact_dir="$(mktemp -d)"
uv build --no-build-isolation --out-dir "$artifact_dir"
uv run --no-sync python scripts/check_pypi_readme.py \
  "$artifact_dir"/*.whl "$artifact_dir"/*.tar.gz
uv run --no-sync twine check --strict \
  "$artifact_dir"/*.whl "$artifact_dir"/*.tar.gz
uv run python scripts/check_distribution.py "$artifact_dir"
uv run python scripts/smoke_distributions.py "$artifact_dir"
```

The distribution check validates both archive contents and the canonical
Support and Security links embedded in each archive's Core Metadata.

Maintainers should also run the hash-pinned public corpus before a release:

```bash
uv run python scripts/check_public_corpus.py
```

See [docs/public-corpus.md](docs/public-corpus.md) for system dependencies,
attribution, licenses, and offline reproduction.

The inventory benchmark creates 64- and 128-spot PDFs only in a temporary
directory. Its page/Form parse counts are deterministic; timing and Python heap
measurements are diagnostic. See [docs/performance.md](docs/performance.md).

Documentation image checks regenerate all five synthetic visuals in a private
temporary directory. A deterministic SHA-256 manifest binds them to the demo,
locked environment, generator, and current spotpdf source. SVGs and that
manifest must match byte for byte. PNGs use bounded decoding and calibrated
pixel-drift limits so harmless Poppler antialiasing differences between
operating systems pass while meaningful visual changes fail: at most 2.5% of
pixels may have an RGB channel delta above 16, and the mean absolute channel
delta may not exceed 2.0.

## Processing budget changes

Any counter, default, or preflight-order change must include:

- an exact-limit success test and a limit-plus-one failure test;
- small runtime-generated fixtures, including compressed content when decoded
  size is involved;
- atomicity coverage proving every mutating command preserves an existing
  `--force` destination and leaves no private temporary file;
- platform-stable assertions on counts and structured errors, not wall-clock,
  RSS, compression ratios, or allocator behavior; and
- synchronized README, processing-budget, security, architecture, and changelog
  documentation.

Keep limit configuration immutable and inject it per public library call. Do
not store per-job overrides or usage in mutable CLI/module globals. New limits
must document what they include, what they exclude, when they are checked, and
which native or operating-system work can occur before the overrun is observed.

## JSON CLI contract changes

Treat `spotpdf.cli/v1` as a public automation API:

- add command fields through explicit serializers, never `dataclasses.asdict()`;
- keep PDF names and paths exact while sorting every set-derived array;
- add subprocess tests for exit code, stdout/stderr purity, canonical JSON,
  Unicode and control characters, and the default text output;
- document every new result field or stable error code in
  [JSON output and automation](docs/json-output.md) and `CHANGELOG.md`; and
- introduce a new schema identifier before removing, renaming, changing the
  type of, or changing the meaning of an existing field.

Human-readable text remains the default. A JSON change must not weaken the
same atomic-output and fail-closed guarantees exercised by text mode.

## PDF fixtures

Tests should build the smallest possible PDF with `pikepdf` inside a temporary
directory. Generated fixtures must not survive the test run.

If a third-party fixture is essential, prefer a commit-pinned external corpus
case over vendoring it. Document all of the following in the pull request:

- canonical source URL;
- author or project;
- exact license and redistribution permission;
- why a synthetic fixture cannot exercise the same behavior.

Do not add customer files, real print jobs, or PDFs with personal information.
The repository and distribution checks reject all tracked `*.pdf` files by
default, including mixed-case extensions. Repository-relative Markdown
destinations and repository-relative targets in HTML `href`, `src`, and
`srcset` attributes may only point to tracked files; ignored local files are
deliberately not accepted. External URLs and URL fragments are outside this
check's scope, but the file path before a fragment is still validated. The
package README has a stricter release gate: repository content must use absolute
URLs bound to the exact release tag so links and images also work on PyPI.
Contribution, support, security-policy, and release-process routes are the
deliberate live-policy exceptions.

## Design rules

- Keep mutations atomic and fail closed on unsupported semantics.
- Add a regression test before or with every bug fix.
- Keep CLI behavior and exit codes backward compatible unless the change is
  explicitly documented.
- Keep modules focused and Python files under 600 lines.
- Update `CHANGELOG.md` for user-visible behavior.
- Preserve vector data; do not introduce implicit rasterization.

See [AGENTS.md](AGENTS.md) for the complete organization guidelines and
[docs/architecture.md](docs/architecture.md) for current boundaries.

## Pull requests

Keep each pull request narrowly scoped. Describe compatibility impact, include
tests and documentation, and disclose the origin and license of every fixture.
By contributing, you agree that your contribution is licensed under the
project's MIT License.
