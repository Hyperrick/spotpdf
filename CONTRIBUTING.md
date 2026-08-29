# Contributing to spotpdf

Thank you for helping make PDF spot-color processing safer and easier to audit.

## Before opening an issue

- Search existing issues.
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
uv run ruff check .
uv run ruff format --check .
uv run python -m unittest discover -s tests -v
uv build
uv run python scripts/check_distribution.py dist
```

## PDF fixtures

Tests should build the smallest possible PDF with `pikepdf` inside a temporary
directory. Generated fixtures must not survive the test run.

If a third-party fixture is essential, document all of the following in the
pull request before adding it:

- canonical source URL;
- author or project;
- exact license and redistribution permission;
- why a synthetic fixture cannot exercise the same behavior.

Do not add customer files, real print jobs, or PDFs with personal information.
The repository and distribution checks reject all `*.pdf` files by default.

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
