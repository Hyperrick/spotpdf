"""PDF color-space resolution and spot declaration discovery."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pikepdf

from .model import ColorSpaceInfo, InspectionReport, SpotKind
from .objects import ObjectTracker

PROCESS_COLORANTS = frozenset({"Cyan", "Magenta", "Yellow", "Black"})
SPECIAL_COLORANTS = frozenset({"All", "None"})
ALL_MODE_PRESERVED_COLORANTS = PROCESS_COLORANTS | SPECIAL_COLORANTS


def pdf_name(value: Any) -> str:
    """Return a decoded PDF Name without its leading slash."""

    text = str(value)
    return text[1:] if text.startswith("/") else text


def resolve_color_space(resources: Any, resource_name: Any) -> ColorSpaceInfo:
    """Resolve a named content-stream color space through a resource dictionary."""

    name = pdf_name(resource_name)
    if name in {"DeviceGray", "DeviceRGB", "DeviceCMYK", "Pattern"}:
        return ColorSpaceInfo(resource_name=name)

    color_spaces = _dictionary_value(resources, "/ColorSpace")
    if color_spaces is None:
        return ColorSpaceInfo(resource_name=name, resolved=False)

    value = _dictionary_value(color_spaces, f"/{name}")
    if value is None:
        return ColorSpaceInfo(resource_name=name, resolved=False)
    return parse_color_space(value, resource_name=name)


def parse_color_space(value: Any, resource_name: str | None = None) -> ColorSpaceInfo:
    """Parse Separation and DeviceN arrays; other spaces return a neutral result."""

    if isinstance(value, pikepdf.Name):
        return ColorSpaceInfo(resource_name=resource_name or pdf_name(value))
    if not isinstance(value, pikepdf.Array) or not value:
        return ColorSpaceInfo(resource_name=resource_name)

    family = pdf_name(value[0])
    if family == "Separation" and len(value) >= 2:
        colorant = pdf_name(value[1])
        kind = SpotKind.SPECIAL if colorant in SPECIAL_COLORANTS else SpotKind.SEPARATION
        return ColorSpaceInfo(kind=kind, colorants=(colorant,), resource_name=resource_name)
    if family == "DeviceN" and len(value) >= 2 and isinstance(value[1], pikepdf.Array):
        colorants = tuple(pdf_name(item) for item in value[1])
        return ColorSpaceInfo(
            kind=SpotKind.DEVICEN,
            colorants=colorants,
            resource_name=resource_name,
        )
    return ColorSpaceInfo(resource_name=resource_name)


def discover_spot_declarations(pdf: pikepdf.Pdf) -> InspectionReport:
    """Discover all reachable Separation and DeviceN colorants."""

    report = InspectionReport()
    tracker = ObjectTracker()
    for value in walk_pdf_object(pdf.Root, tracker):
        info = parse_color_space(value)
        if info.kind is None:
            continue
        for colorant in info.colorants:
            if info.kind is SpotKind.DEVICEN and colorant in PROCESS_COLORANTS:
                continue
            summary = report.get_or_create(colorant)
            summary.kinds.add(info.kind)
    return report


def all_mode_targets(report: InspectionReport) -> frozenset[str]:
    """Return named spots removed by --all, excluding process and special names."""

    return frozenset(report.spots) - ALL_MODE_PRESERVED_COLORANTS


def walk_pdf_object(value: Any, tracker: ObjectTracker) -> Iterator[Any]:
    """Walk reachable PDF arrays and dictionaries without reading stream bytes."""

    stack = [value]
    while stack:
        current = stack.pop()
        if not isinstance(current, (pikepdf.Array, pikepdf.Dictionary, pikepdf.Stream)):
            continue
        if not tracker.visit(current):
            continue
        yield current

        children = current if isinstance(current, pikepdf.Array) else current.values()
        stack.extend(reversed(list(children)))


def resource_aliases_for_spot(resources: Any, spot: str) -> dict[str, ColorSpaceInfo]:
    """Return aliases whose definitions contain one target colorant."""

    return resource_aliases_for_spots(resources, frozenset({spot}))


def resource_aliases_for_spots(resources: Any, spots: frozenset[str]) -> dict[str, ColorSpaceInfo]:
    """Return aliases whose definitions contain any target colorant."""

    aliases: dict[str, ColorSpaceInfo] = {}
    color_spaces = _dictionary_value(resources, "/ColorSpace")
    if color_spaces is None:
        return aliases
    for name, value in color_spaces.items():
        decoded = pdf_name(name)
        info = parse_color_space(value, resource_name=decoded)
        if info.contains_any(spots):
            aliases[decoded] = info
    return aliases


def remove_spot_resource_aliases(resources: Any, spot: str) -> int:
    """Delete aliases for one target from a resource dictionary."""

    return remove_spot_resource_aliases_for_spots(resources, frozenset({spot}))


def remove_spot_resource_aliases_for_spots(resources: Any, spots: frozenset[str]) -> int:
    """Delete aliases for any target from a resource dictionary."""

    color_spaces = _dictionary_value(resources, "/ColorSpace")
    if not isinstance(color_spaces, pikepdf.Dictionary):
        return 0
    names = [
        name for name, value in color_spaces.items() if parse_color_space(value).contains_any(spots)
    ]
    for name in names:
        del color_spaces[name]
    return len(names)


def _dictionary_value(dictionary: Any, name: str) -> Any | None:
    if not isinstance(dictionary, (pikepdf.Dictionary, pikepdf.Stream)):
        return None
    try:
        return dictionary.get(pikepdf.Name(name), None)
    except (KeyError, TypeError, ValueError):
        return None
