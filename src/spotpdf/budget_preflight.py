"""Fixed-order orchestration for source-PDF processing budgets."""

from __future__ import annotations

from dataclasses import dataclass
from os import PathLike
from pathlib import Path

import pikepdf

from .budget_content import audit_content
from .budget_graph import audit_reachable_graph
from .limits import ProcessingLimits, enforce_limit
from .model import InvalidPdfError


@dataclass(frozen=True)
class ProcessingUsage:
    """Deterministic measurements from one complete input preflight."""

    input_bytes: int
    pages: int
    reachable_objects: int
    decoded_content_bytes: int
    operators: int


def enforce_input_size(path: str | PathLike[str], limits: ProcessingLimits) -> int:
    """Check the filesystem size without reading or opening the PDF."""

    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise InvalidPdfError(f"input PDF does not exist: {resolved}")
    input_bytes = resolved.stat().st_size
    enforce_limit(limits, "input_bytes", input_bytes)
    return input_bytes


def audit_pdf(
    pdf: pikepdf.Pdf,
    limits: ProcessingLimits,
    *,
    input_bytes: int,
) -> ProcessingUsage:
    """Run page, graph, decoded-content, and operator checks in fixed order."""

    pages = len(pdf.pages)
    enforce_limit(limits, "pages", pages)
    graph = audit_reachable_graph(pdf, limits)
    content = audit_content(pdf, graph.forms, limits)
    return ProcessingUsage(
        input_bytes=input_bytes,
        pages=pages,
        reachable_objects=graph.reachable_objects,
        decoded_content_bytes=content.decoded_content_bytes,
        operators=content.operators,
    )


__all__ = ["ProcessingUsage", "audit_pdf", "enforce_input_size"]
