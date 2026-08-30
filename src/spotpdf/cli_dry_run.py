"""Temporary destinations for fully verified CLI mutation dry runs."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def mutation_destination(
    output_path: Path | None,
    *,
    dry_run: bool,
) -> Iterator[Path]:
    """Yield a real destination or an automatically discarded dry-run PDF path."""

    if dry_run:
        if output_path is not None:
            raise ValueError("a dry run must not have an output path")
        with tempfile.TemporaryDirectory(prefix="spotpdf-dry-run-") as directory:
            yield Path(directory) / "verified-output.pdf"
        return

    if output_path is None:
        raise ValueError("a mutation requires an output path or --dry-run")
    yield output_path


__all__ = ["mutation_destination"]
