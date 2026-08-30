"""Atomic orchestration for changing Separation alternate CMYK previews."""

from __future__ import annotations

from collections.abc import Sequence
from os import PathLike
from pathlib import Path
from typing import Any

import pikepdf

from .alternate_plan import (
    AlternatePlan,
    NormalizedCmyk,
    build_alternate_plan,
)
from .cmyk import (
    PercentageCmyk,
    normalized_cmyk,
    parse_cmyk_percentages,
    validate_cmyk_percentages,
)
from .inventory import discover_spot_declarations
from .limits import DEFAULT_PROCESSING_LIMITS, ProcessingLimits, require_processing_limits
from .model import AlternateResult, SpotPdfError
from .mutation_verification import (
    ContentFingerprint,
    InventoryFingerprint,
    content_fingerprint,
    inventory_fingerprint,
    parse_content_streams,
)
from .publication import atomic_pdf_output, open_strict, save_pdf
from .rename_slots import semantic_pdf_fingerprint
from .scan import validate_document_for_mutation


def set_alternate_cmyk(
    input_path: str | PathLike[str],
    output_path: str | PathLike[str],
    spot: str,
    cmyk: Sequence[object],
    *,
    force: bool = False,
    limits: ProcessingLimits = DEFAULT_PROCESSING_LIMITS,
) -> AlternateResult:
    """Replace every matching Separation preview with one linear CMYK fallback."""

    input_path = Path(input_path)
    output_path = Path(output_path)
    limits = require_processing_limits(limits)
    percentages = validate_cmyk_percentages(cmyk)
    normalized = normalized_cmyk(percentages)
    stored_percentages: PercentageCmyk = (
        normalized[0] * 100,
        normalized[1] * 100,
        normalized[2] * 100,
        normalized[3] * 100,
    )
    result: AlternateResult | None = None
    with atomic_pdf_output(input_path, output_path, force=force, limits=limits) as output:
        with open_strict(output.input_path, limits=limits) as pdf:
            validate_document_for_mutation(pdf)
            before = discover_spot_declarations(pdf)
            plan = build_alternate_plan(pdf, before, spot, normalized)
            expected_inventory = inventory_fingerprint(before)
            expected_content = content_fingerprint(pdf)
            expected_masked_document = plan.normalized_document_fingerprint()

            plan.apply()
            _verify_in_memory(
                pdf,
                plan,
                expected_inventory,
                expected_content,
                expected_masked_document,
            )
            expected_saved_document = semantic_pdf_fingerprint(pdf)
            save_pdf(pdf, output.temp_path)
            result = AlternateResult(
                spot=spot,
                cmyk_percentages=stored_percentages,
                definitions_changed=plan.definitions_changed,
            )

        _verify_saved_pdf(
            output.temp_path,
            spot,
            normalized,
            expected_inventory,
            expected_content,
            expected_saved_document,
            result.definitions_changed,
        )

    if result is None:  # pragma: no cover - guarded by the transaction above
        raise SpotPdfError("alternate preview change did not produce a result")
    return result


def _verify_in_memory(
    pdf: pikepdf.Pdf,
    plan: AlternatePlan,
    expected_inventory: InventoryFingerprint,
    expected_content: ContentFingerprint,
    expected_masked_document: tuple[Any, ...],
) -> None:
    plan.verify_requested()
    if inventory_fingerprint(discover_spot_declarations(pdf)) != expected_inventory:
        raise SpotPdfError("spot inventory changed while setting the alternate preview")
    if content_fingerprint(pdf) != expected_content:
        raise SpotPdfError("content streams changed while setting the alternate preview")
    if plan.normalized_document_fingerprint() != expected_masked_document:
        raise SpotPdfError("PDF semantics changed beyond the planned alternate preview slots")


def _verify_saved_pdf(
    path: Path,
    spot: str,
    cmyk: NormalizedCmyk,
    expected_inventory: InventoryFingerprint,
    expected_content: ContentFingerprint,
    expected_document: tuple[Any, ...],
    expected_definitions: int,
) -> None:
    with open_strict(path, limits=None) as pdf:
        report = discover_spot_declarations(pdf)
        if inventory_fingerprint(report) != expected_inventory:
            raise SpotPdfError("saved PDF spot inventory changed unexpectedly")
        plan = build_alternate_plan(pdf, report, spot, cmyk)
        if plan.definitions_changed != expected_definitions:
            raise SpotPdfError("saved PDF has a different number of target Separations")
        plan.verify_requested()
        if content_fingerprint(pdf) != expected_content:
            raise SpotPdfError("saved PDF content streams changed unexpectedly")
        parse_content_streams(pdf)
        if semantic_pdf_fingerprint(pdf) != expected_document:
            raise SpotPdfError("saved PDF object semantics changed during rewrite")


__all__ = [
    "PercentageCmyk",
    "parse_cmyk_percentages",
    "set_alternate_cmyk",
    "validate_cmyk_percentages",
]
