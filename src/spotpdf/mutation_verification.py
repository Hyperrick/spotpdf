"""Shared semantic fingerprints for atomic PDF mutations."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from typing import Any, TypeAlias

import pikepdf

from .inventory_graph import walk_reachable
from .model import InspectionReport, SpotPdfError
from .objects import object_key


@dataclass(frozen=True)
class InventoryFingerprint:
    """Location-independent semantic colorant inventory."""

    colorants: tuple[tuple[Any, ...], ...]
    definitions: tuple[tuple[Any, ...], ...]
    dependencies: tuple[tuple[Any, ...], ...]


ContentFingerprint: TypeAlias = Counter[tuple[str, tuple[str, ...], bytes]]


def inventory_fingerprint(
    report: InspectionReport,
    *,
    normalize: tuple[str, str] | None = None,
) -> InventoryFingerprint:
    """Create a deterministic inventory signature with optional name normalization."""

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
    return InventoryFingerprint(colorants, definitions, dependencies)


def content_fingerprint(pdf: pikepdf.Pdf) -> ContentFingerprint:
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
    fingerprints: ContentFingerprint = Counter()
    for kind, digest, locations in records.values():
        fingerprints[(kind, tuple(sorted(locations)), digest)] += 1
    return fingerprints


def parse_content_streams(pdf: pikepdf.Pdf) -> None:
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


def _is_contents_path(location: str) -> bool:
    marker = " /Contents"
    start = location.find(marker)
    while start >= 0:
        following = start + len(marker)
        if following == len(location) or location[following] in " [":
            return True
        start = location.find(marker, start + 1)
    return False


__all__ = [
    "ContentFingerprint",
    "InventoryFingerprint",
    "content_fingerprint",
    "inventory_fingerprint",
    "parse_content_streams",
]
