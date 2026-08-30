"""Semantic PDF trailer entries shared by verification and owner checks."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pikepdf

_STORAGE_KEYS = frozenset(
    {
        pikepdf.Name.ID,
        pikepdf.Name.Prev,
        pikepdf.Name.Size,
        pikepdf.Name.XRefStm,
    }
)


def semantic_trailer_items(pdf: pikepdf.Pdf) -> Iterator[tuple[pikepdf.Name, Any]]:
    """Yield trailer entries whose values are part of document semantics."""

    yield from (
        (key, value)
        for key, value in sorted(pdf.trailer.items(), key=lambda item: str(item[0]))
        if key not in _STORAGE_KEYS
    )


__all__ = ["semantic_trailer_items"]
