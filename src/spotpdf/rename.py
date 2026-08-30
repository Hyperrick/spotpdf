"""Atomic orchestration and verification for exact spot-plate renames."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, TypeAlias

import pikepdf

from .inventory import discover_spot_declarations
from .inventory_graph import walk_reachable
from .inventory_values import name_value
from .model import InspectionReport, RenameResult, SpotPdfError
from .mutation_verification import (
    ContentFingerprint,
    InventoryFingerprint,
    content_fingerprint,
    inventory_fingerprint,
    parse_content_streams,
)
from .objects import object_key
from .publication import atomic_pdf_output, open_strict, save_pdf
from .rename_plan import build_rename_plan
from .rename_slots import (
    RenamePlan,
    normalize_rename_location,
    semantic_object_fingerprint,
    semantic_pdf_fingerprint,
)
from .scan import validate_document_for_mutation

_PreviewFingerprint: TypeAlias = Counter[
    tuple[str, tuple[str, ...], tuple[Any, ...], tuple[Any, ...]]
]
_PlanFingerprint: TypeAlias = tuple[tuple[Any, ...], ...]
_DocumentFingerprint: TypeAlias = tuple[Any, ...]


def rename_spot(
    input_path: Path,
    output_path: Path,
    source: str,
    destination: str,
    *,
    force: bool = False,
) -> RenameResult:
    """Rename one exact Separation plate and all supported name dependencies."""

    result: RenameResult | None = None
    with atomic_pdf_output(input_path, output_path, force=force) as output:
        with open_strict(output.input_path) as pdf:
            validate_document_for_mutation(pdf)
            before = discover_spot_declarations(pdf)
            plan = build_rename_plan(pdf, before, source, destination)
            expected_inventory = inventory_fingerprint(before)
            expected_content = content_fingerprint(pdf)
            expected_preview = _preview_fingerprint(pdf, source)
            expected_plan = plan.preflight_fingerprint()
            expected_apply_document = plan.normalized_document_fingerprint(pdf)

            plan.apply()
            plan.verify_invariants()
            after = discover_spot_declarations(pdf)
            _verify_inventory(
                after,
                expected_inventory,
                source,
                destination,
            )
            _verify_plan_semantics(pdf, after, destination, source, expected_plan)
            if content_fingerprint(pdf) != expected_content:
                raise SpotPdfError("rename unexpectedly changed PDF content streams")
            _verify_preview(pdf, destination, expected_preview)
            _verify_apply_semantics(pdf, plan, expected_apply_document)
            expected_saved_document = semantic_pdf_fingerprint(pdf)
            save_pdf(pdf, output.temp_path)
            result = RenameResult(
                source=source,
                destination=destination,
                definitions_renamed=plan.definitions_renamed,
                references_renamed=plan.references_renamed,
            )

        _verify_saved_pdf(
            output.temp_path,
            expected_inventory,
            expected_content,
            expected_preview,
            expected_plan,
            expected_saved_document,
            source,
            destination,
        )

    if result is None:  # pragma: no cover - guarded by the transaction above
        raise SpotPdfError("rename did not produce a result")
    return result


def _verify_saved_pdf(
    path: Path,
    expected_inventory: InventoryFingerprint,
    expected_content: ContentFingerprint,
    expected_preview: _PreviewFingerprint,
    expected_plan: _PlanFingerprint,
    expected_document: _DocumentFingerprint,
    source: str,
    destination: str,
) -> None:
    """Reopen a candidate output and verify semantics before publication."""

    with open_strict(path) as pdf:
        report = discover_spot_declarations(pdf)
        _verify_inventory(
            report,
            expected_inventory,
            source,
            destination,
        )
        _verify_plan_semantics(pdf, report, destination, source, expected_plan)
        if content_fingerprint(pdf) != expected_content:
            raise SpotPdfError("saved PDF content streams differ after rename")
        _verify_preview(pdf, destination, expected_preview)
        parse_content_streams(pdf)
        _verify_saved_document_semantics(pdf, expected_document)


def _verify_apply_semantics(
    pdf: pikepdf.Pdf,
    plan: RenamePlan,
    expected: _DocumentFingerprint,
) -> None:
    """Require in-memory mutation to differ only at the plan's exact slots."""

    actual = plan.normalized_document_fingerprint(pdf)
    if actual != expected:
        raise SpotPdfError("PDF object semantics changed beyond the planned name slots")


def _verify_saved_document_semantics(
    pdf: pikepdf.Pdf,
    expected: _DocumentFingerprint,
) -> None:
    """Require the saved candidate to preserve all post-rename PDF semantics."""

    if semantic_pdf_fingerprint(pdf) != expected:
        raise SpotPdfError("saved PDF object semantics changed during rewrite")


def _verify_plan_semantics(
    pdf: pikepdf.Pdf,
    report: InspectionReport,
    source: str,
    destination: str,
    expected: _PlanFingerprint,
) -> None:
    """Re-plan the inverse alias and require every mutation context to be unchanged."""

    reverse = build_rename_plan(pdf, report, source, destination)
    if reverse.preflight_fingerprint() != expected:
        raise SpotPdfError("rename dependency values or definition contexts changed")


def _verify_inventory(
    report: InspectionReport,
    expected: InventoryFingerprint,
    source: str,
    destination: str,
) -> None:
    """Require an exact semantic old-to-new substitution and no other changes."""

    if source in report.colorants or any(item.name == source for item in report.dependencies):
        raise SpotPdfError(f"post-rename validation found stale source name {source!r}")
    if destination not in report.colorants:
        raise SpotPdfError(f"post-rename validation did not find target name {destination!r}")
    actual = inventory_fingerprint(report, normalize=(destination, source))
    if actual != expected:
        raise SpotPdfError("post-rename semantic inventory differs beyond the requested name")


def _preview_fingerprint(
    pdf: pikepdf.Pdf,
    normalize_name: str,
) -> _PreviewFingerprint:
    """Capture every Separation/DeviceN preview, normalizing one renamed key path."""

    records: dict[
        tuple[Any, ...],
        tuple[str, tuple[Any, ...], tuple[Any, ...], set[str]],
    ] = {}
    for visit in walk_reachable(pdf):
        value = visit.value
        if not isinstance(value, pikepdf.Array) or len(value) < 4:
            continue
        family = name_value(value[0])
        if family == "Separation":
            matches = len(value) == 4 and name_value(value[1]) is not None
        elif family == "DeviceN":
            components = value[1]
            matches = isinstance(components, pikepdf.Array) and all(
                name_value(item) is not None for item in components
            )
        else:
            matches = False
        if not matches:
            continue
        key = object_key(value)
        identity = key if key[0] == "indirect" else ("direct", min(visit.locations))
        alternate = semantic_object_fingerprint(value[2])
        tint = semantic_object_fingerprint(value[3])
        record = records.setdefault(identity, (family, alternate, tint, set()))
        if record[:3] != (family, alternate, tint):
            raise SpotPdfError("one color-space preview changed while it was being inspected")
        record[3].update(
            normalize_rename_location(item, normalize_name) for item in visit.locations
        )
    previews: _PreviewFingerprint = Counter()
    for family, alternate, tint, locations in records.values():
        previews[(family, tuple(sorted(locations)), alternate, tint)] += 1
    return previews


def _verify_preview(
    pdf: pikepdf.Pdf,
    name: str,
    expected: _PreviewFingerprint,
) -> None:
    if _preview_fingerprint(pdf, name) != expected:
        raise SpotPdfError("color-space alternate spaces or tint transforms changed during rename")
