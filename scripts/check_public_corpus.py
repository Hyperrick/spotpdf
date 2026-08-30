"""Run spotpdf against the pinned public prepress PDF corpus."""

from __future__ import annotations

import argparse
from pathlib import Path

from public_corpus import run_public_corpus


def main() -> None:
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=repository / "corpus" / "manifest.toml",
        help="pinned TOML corpus manifest",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=repository / "tmp" / "public-corpus",
        help="download cache (PDFs remain ignored by Git)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="require every hash-verified PDF to exist in the cache",
    )
    args = parser.parse_args()
    run_public_corpus(
        args.manifest.resolve(),
        args.cache.resolve(),
        offline=args.offline,
    )


if __name__ == "__main__":
    main()
