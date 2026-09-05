# Reproduce the visual diagnosis example

The fictional NORD coffee brochure is generated entirely from code, with English
text, vector artwork, and two raster seals using the `FOIL_GOLD` separation.
ReportLab is a development dependency; no external assets or system fonts are
required. The artwork is part of this MIT-licensed repository.

From the repository root:

```sh
uv sync --locked --dev
uv run python examples/create_report_demo.py out/nord-coffee.pdf
uv run spotpdf remove out/nord-coffee.pdf --spot FOIL_GOLD --dry-run --report out/nord-report.html
```

Exit code 1 is expected: removal refuses spot-color images. Open the generated
HTML locally. It shows the first refusal, a second independently found seal,
original-page previews, numbered bounds, enlarged excerpts, and technical
locations. It also states the remaining investigation gaps. No PDF is modified.
For a repeated run, add `--report-overwrite` to replace the previous report.

## Refresh the README screenshots

Install Node.js (providing `npx`) and Chromium for the pinned Playwright CLI:

```sh
npx --yes --package @playwright/cli@0.1.19 playwright-cli install-browser
uv run python scripts/create_report_example.py
uv run python scripts/create_report_example.py --check
```

The capture script generates a private temporary PDF and report, verifies both
seal locations, and captures actual report elements in Chromium with the network
offline. It writes `finding.png`, `page-location.png`, and a hash manifest into
`docs/report-example/`. Inspect both screenshots after regeneration. The manifest
records source hashes, image hashes, dimensions, and capture settings. `--check`
verifies that provenance and image integrity, without requiring a browser.

Generated PDFs and HTML stay in ignored output directories or temporary storage.
Only the generator, documentation screenshots, and capture manifest are tracked.
