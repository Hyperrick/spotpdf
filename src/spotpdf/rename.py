"""Atomic orchestration and verification for exact spot-plate renames."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

import pikepdf

from .inventory import discover_spot_declarations
from .inventory_graph import walk_reachable
from .inventory_values import name_value
from .model import InspectionReport, RenameResult, SpotPdfError
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


@dataclass(frozen=True)
class _InventoryFingerprint:
    """Location-independent semantic inventory used across a saved rewrite."""

    colorants: tuple[tuple[Any, ...], ...]
    definitions: tuple[tuple[Any, ...], ...]
    dependencies: tuple[tuple[Any, ...], ...]


_ContentFingerprint: TypeAlias = Counter[tuple[str, tuple[str, ...], bytes]]
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
            expected_inventory = _inventory_fingerprint(before)
            expected_content = _content_fingerprint(pdf)
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
            if _content_fingerprint(pdf) != expected_content:
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
    expected_inventory: _InventoryFingerprint,
    expected_content: _ContentFingerprint,
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
        if _content_fingerprint(pdf) != expected_content:
            raise SpotPdfError("saved PDF content streams differ after rename")
        _verify_preview(pdf, destination, expected_preview)
        _parse_content_streams(pdf)
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
    expected: _InventoryFingerprint,
    source: str,
    destination: str,
) -> None:
    """Require an exact semantic old-to-new substitution and no other changes."""

    if source in report.colorants or any(item.name == source for item in report.dependencies):
        raise SpotPdfError(f"post-rename validation found stale source name {source!r}")
    if destination not in report.colorants:
        raise SpotPdfError(f"post-rename validation did not find target name {destination!r}")
    actual = _inventory_fingerprint(report, normalize=(destination, source))
    if actual != expected:
        raise SpotPdfError("post-rename semantic inventory differs beyond the requested name")


def _inventory_fingerprint(
    report: InspectionReport,
    *,
    normalize: tuple[str, str] | None = None,
) -> _InventoryFingerprint:
    """Create a deterministic semantic signature, excluding expected path changes."""

    def name(value: str) -> str:
        if normalize is not None and value == normalize[0]:
            return normalize[1]
        return value

    colorants = tuple(
        sorted(
            (
                name(colorant_name),
                tuple(sorted(role.value for role in summary.roles)),
                tuple(sorted(kind.value for kind in summary.kinds)),
            )
            for colorant_name, summary in report.colorants.items()
        )
    )
    definitions = tuple(
        sorted(
            (
                definition.kind.value,
                tuple((name(item.name), item.role.value) for item in definition.components),
                definition.subtype or "",
                definition.process_color_space or "",
                tuple(name(item) for item in definition.process_components),
                tuple(sorted(name(item) for item in definition.individual_colorants)),
            )
            for definition in report.definitions.values()
        )
    )
    dependency_counts = Counter((name(item.name), item.kind.value) for item in report.dependencies)
    dependencies = tuple(
        sorted(
            (dependency_name, kind, count)
            for (dependency_name, kind), count in dependency_counts.items()
        )
    )
    return _InventoryFingerprint(colorants, definitions, dependencies)


def _content_fingerprint(pdf: pikepdf.Pdf) -> _ContentFingerprint:
    """Hash every page, Form, and appearance content stream without rewriting it."""

    records: dict[tuple[Any, ...], tuple[str, bytes, set[str]]] = {}
    for visit in walk_reachable(pdf):
        value = visit.value
        if not isinstance(value, pikepdf.Stream):
            continue
        key = object_key(value)
        subtype = value.get(pikepdf.Name.Subtype, None)
        if subtype == pikepdf.Name.Form:
            kind = "Form"
        elif any(_is_contents_path(location) for location in visit.locations):
            kind = "Page"
        else:
            continue
        identity = key if key[0] == "indirect" else ("direct", min(visit.locations))
        digest = hashlib.sha256(value.read_bytes()).digest()
        record = records.setdefault(identity, (kind, digest, set()))
        if record[:2] != (kind, digest):
            raise SpotPdfError("one content stream changed while it was being inspected")
        record[2].update(visit.locations)
    fingerprints: _ContentFingerprint = Counter()
    for kind, digest, locations in records.values():
        fingerprints[(kind, tuple(sorted(locations)), digest)] += 1
    return fingerprints


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


def _is_contents_path(location: str) -> bool:
    marker = " /Contents"
    start = location.find(marker)
    while start >= 0:
        following = start + len(marker)
        if following == len(location) or location[following] in " [":
            return True
        start = location.find(marker, start + 1)
    return False


def _parse_content_streams(pdf: pikepdf.Pdf) -> None:
    """Require every page and reachable Form stream to remain parseable."""

    for page in pdf.pages:
        pikepdf.parse_content_stream(page)
    seen: set[tuple[Any, ...]] = set()
    for visit in walk_reachable(pdf):
        value = visit.value
        if not isinstance(value, pikepdf.Stream):
            continue
        if value.get(pikepdf.Name.Subtype, None) != pikepdf.Name.Form:
            continue
        key = object_key(value)
        if key in seen:
            continue
        seen.add(key)
        pikepdf.parse_content_stream(value)
