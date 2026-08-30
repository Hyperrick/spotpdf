"""Atomic public API for converting one Separation to explicit DeviceCMYK paint."""

from __future__ import annotations

from collections.abc import Sequence
from os import PathLike
from pathlib import Path
from typing import Any

import pikepdf

from .cmyk import PercentageCmyk, normalized_cmyk, validate_cmyk_percentages
from .convert_plan import ConversionPlan, build_conversion_plan
from .inventory import discover_spot_declarations
from .limits import DEFAULT_PROCESSING_LIMITS, ProcessingLimits, require_processing_limits
from .model import ConversionResult, InspectionReport, SpotPdfError
from .mutation_verification import (
    ContentFingerprint,
    InventoryFingerprint,
    content_fingerprint,
    inventory_fingerprint,
    parse_content_streams,
)
from .publication import atomic_pdf_output, open_strict, save_pdf
from .rename_slots import semantic_pdf_fingerprint
from .scan import validate_document_for_changes


def convert_spot_to_cmyk(
    input_path: str | PathLike[str],
    output_path: str | PathLike[str],
    spot: str,
    cmyk: Sequence[object],
    *,
    force: bool = False,
    limits: ProcessingLimits = DEFAULT_PROCESSING_LIMITS,
) -> ConversionResult:
    """Replace supported Separation paint with the requested explicit CMYK recipe."""

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
    result: ConversionResult | None = None
    with atomic_pdf_output(input_path, output_path, force=force, limits=limits) as output:
        with open_strict(output.input_path, limits=limits) as pdf:
            before = discover_spot_declarations(pdf)
            validate_document_for_changes(
                pdf,
                frozenset({spot}),
                declarations=before,
            )
            plan = build_conversion_plan(pdf, before, spot, normalized)
            expected_masked_document = plan.normalized_document_fingerprint(
                pdf,
                applied=False,
            )

            plan.apply()
            after = _verify_in_memory(
                pdf,
                plan,
                expected_masked_document,
            )
            expected_inventory = inventory_fingerprint(after)
            expected_content = content_fingerprint(pdf)
            expected_document = semantic_pdf_fingerprint(pdf)
            save_pdf(pdf, output.temp_path)
            result = ConversionResult(
                spot=spot,
                cmyk_percentages=stored_percentages,
                definitions_removed=plan.definitions_removed,
                resources_removed=plan.resources_removed,
                page_content_sequences_changed=(plan.streams.page_content_sequences_changed),
                forms_changed=plan.streams.forms_changed,
                color_operators_rewritten=plan.streams.color_operators_rewritten,
                pages_affected=plan.streams.pages_affected,
            )

        _verify_saved_pdf(
            output.temp_path,
            spot,
            expected_inventory,
            expected_content,
            expected_document,
        )

    if result is None:  # pragma: no cover - guarded by the transaction above
        raise SpotPdfError("CMYK conversion did not produce a result")
    return result


def _verify_in_memory(
    pdf: pikepdf.Pdf,
    plan: ConversionPlan,
    expected_masked_document: tuple[Any, ...],
) -> InspectionReport:
    plan.verify_applied()
    if plan.normalized_document_fingerprint(pdf, applied=True) != expected_masked_document:
        raise SpotPdfError("PDF semantics changed beyond the planned CMYK conversion")
    parse_content_streams(pdf)
    report = discover_spot_declarations(pdf)
    _require_target_absent(report, plan.spot, "converted PDF")
    return report


def _verify_saved_pdf(
    path: Path,
    spot: str,
    expected_inventory: InventoryFingerprint,
    expected_content: ContentFingerprint,
    expected_document: tuple[Any, ...],
) -> None:
    with open_strict(path, limits=None) as pdf:
        report = discover_spot_declarations(pdf)
        _require_target_absent(report, spot, "saved PDF")
        if inventory_fingerprint(report) != expected_inventory:
            raise SpotPdfError("saved PDF spot inventory changed unexpectedly")
        if content_fingerprint(pdf) != expected_content:
            raise SpotPdfError("saved PDF content streams changed unexpectedly")
        parse_content_streams(pdf)
        if semantic_pdf_fingerprint(pdf) != expected_document:
            raise SpotPdfError("saved PDF object semantics changed during rewrite")


def _require_target_absent(
    report: InspectionReport,
    spot: str,
    label: str,
) -> None:
    dependencies = [item for item in report.dependencies if item.name == spot]
    if spot in report.colorants or dependencies:
        raise SpotPdfError(f"{label} still contains target spot color {spot!r}")


__all__ = ["convert_spot_to_cmyk"]
