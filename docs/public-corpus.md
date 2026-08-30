# Public prepress corpus gate

The unit suite creates every PDF fixture at runtime. A separate release gate
tests `spotpdf` against six public prepress PDFs without committing third-party
PDFs to this repository. [`corpus/manifest.toml`](../corpus/manifest.toml) pins
each upstream commit, raw URL, byte size, SHA-256 digest, license, and expected
operation.

## Coverage

| Upstream case | License | Release assertion |
| --- | --- | --- |
| Ghostscript `examples/spots2.pdf` | AGPL-3.0-or-later | `remove --all` removes three painted custom plates. |
| veraPDF t01-pass-b | CC BY 4.0 | Rename with a DeviceCMYK alternate stays pixel-identical. |
| veraPDF t01-pass-c | CC BY 4.0 | DeviceN components and `/Colorants` dependencies rename together. |
| veraPDF t01-pass-f | CC BY 4.0 | Rename preserves a DeviceRGB alternate and composite appearance. |
| veraPDF t02-pass-a | CC BY 4.0 | `remove --all` preserves arbitrary NChannel process components as a byte-identical no-op. |
| veraPDF t03-pass-a | CC BY 4.0 | Four matching definitions over two pages rename consistently. |

The veraPDF files come from the commit-pinned
[`veraPDF-corpus`](https://github.com/veraPDF/veraPDF-corpus/tree/01e40281d48e2f3755006fdf596ca25caaea8634),
whose README licenses the corpus under CC BY 4.0. The Ghostscript file comes
from the commit-pinned
[`ghostpdl` examples directory](https://github.com/ArtifexSoftware/ghostpdl/tree/f13745a17ff7af385d70d230c3d8594e501d6b6b/examples),
which its LICENSE covers under AGPL-3.0-or-later. The source PDFs are downloaded
only for the gate and are not redistributed by this project.

## Run locally

Install qpdf, Poppler, and Ghostscript. On macOS with Homebrew:

```bash
brew install qpdf poppler ghostscript
uv run python scripts/check_public_corpus.py
```

On Debian or Ubuntu:

```bash
sudo apt-get update
sudo apt-get install --yes qpdf poppler-utils ghostscript
uv run python scripts/check_public_corpus.py
```

Downloads go to the ignored `tmp/public-corpus/` cache. Every cached file is
accepted only when both its exact byte count and SHA-256 digest match the
manifest. Re-run without network access after a successful download:

```bash
uv run python scripts/check_public_corpus.py --offline
```

For every case the runner executes `qpdf --check`, renders real separation
plates with Ghostscript `tiffsep`, and validates the post-operation inventory.
Rename cases additionally compare all Poppler-rendered pages byte-for-byte
within the same run. The gate deliberately does not store golden raster hashes,
because different RIP versions may produce different but internally consistent
raster files.
