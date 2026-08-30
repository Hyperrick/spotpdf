"""Fail-closed dependency checks for color-space aliases planned for deletion."""

from __future__ import annotations

from typing import Any

import pikepdf

from .colors import pdf_name
from .content_support import operator_name
from .convert_resources import ColorSpaceRemoval
from .inventory_graph import walk_reachable
from .inventory_values import path_name, separation_name
from .model import UnsupportedSpotUseError
from .objects import ObjectKey, ObjectTracker, object_key

_DIRECT_COLOR_SPACE_NAMES = frozenset({"DeviceGray", "DeviceRGB", "DeviceCMYK", "Pattern"})


class _AliasDependencyScanner:
    """Inspect color-space consumers under their effective resource scopes."""

    def __init__(
        self,
        pdf: pikepdf.Pdf,
        spot: str,
        removals: tuple[ColorSpaceRemoval, ...],
    ) -> None:
        self.pdf = pdf
        self.spot = spot
        self.removed_aliases = frozenset(pdf_name(removal.key) for removal in removals)
        self.scanned_contexts: set[tuple[ObjectKey, tuple[str, ...]]] = set()
        self.scanned_resources: set[ObjectKey] = set()
        self.contextualized: set[ObjectKey] = set()
        self.retained_direct_objects: list[Any] = []

    def scan(self) -> None:
        for page_number, page in enumerate(self.pdf.pages, start=1):
            resources = page.obj.get(pikepdf.Name.Resources, None)
            if not isinstance(resources, pikepdf.Dictionary):
                continue
            aliases = self._scan_resources(resources, f"page {page_number} /Resources")
            group = page.obj.get(pikepdf.Name.Group, None)
            if group is not None:
                self._scan_value(group, aliases, f"page {page_number} /Group")

        for visit in walk_reachable(self.pdf):
            value = visit.value
            if not isinstance(value, (pikepdf.Dictionary, pikepdf.Stream)):
                continue
            key = object_key(value)
            if (
                key in self.contextualized
                or any(location.startswith("page ") for location in visit.locations)
                or not _needs_context_fallback(value)
            ):
                continue
            self._scan_value(value, self.removed_aliases, min(visit.locations))

    def _scan_resources(self, resources: pikepdf.Dictionary, location: str) -> frozenset[str]:
        aliases = _resource_target_aliases(resources, self.spot, self.removed_aliases)
        key = object_key(resources)
        self._retain_direct(resources, key)
        self.contextualized.add(key)
        if key in self.scanned_resources:
            return aliases
        self.scanned_resources.add(key)

        color_spaces = resources.get(pikepdf.Name.ColorSpace, None)
        if isinstance(color_spaces, pikepdf.Dictionary):
            for name, value in color_spaces.items():
                if separation_name(value) == self.spot:
                    continue
                hits = _color_space_alias_hits(value, aliases)
                if hits:
                    _raise_dependency(
                        hits,
                        f"{location} /ColorSpace {path_name(name)}",
                        "color-space resource",
                    )

        for name, value in resources.items():
            if name == pikepdf.Name.ColorSpace:
                continue
            self._scan_value(value, aliases, f"{location} {path_name(name)}")
        return aliases

    def _scan_value(self, value: Any, aliases: frozenset[str], location: str) -> None:
        if not isinstance(value, (pikepdf.Array, pikepdf.Dictionary, pikepdf.Stream)):
            return
        key = object_key(value)
        self._retain_direct(value, key)
        context = (key, tuple(sorted(aliases)))
        if context in self.scanned_contexts:
            return
        self.scanned_contexts.add(context)
        self.contextualized.add(key)

        if isinstance(value, pikepdf.Array):
            for index, child in enumerate(value):
                self._scan_value(child, aliases, f"{location}[{index}]")
            return

        active_aliases = aliases
        resources = value.get(pikepdf.Name.Resources, None)
        if isinstance(resources, pikepdf.Dictionary):
            active_aliases = self._scan_resources(resources, f"{location} /Resources")

        self._reject_named_color_slot(value, pikepdf.Name.ColorSpace, active_aliases, location)
        self._reject_named_color_slot(value, pikepdf.Name.CS, active_aliases, location)
        self._reject_tiling_pattern_content(value, active_aliases, location)

        for name, child in value.items():
            if name in {pikepdf.Name.Resources, pikepdf.Name.ColorSpace, pikepdf.Name.CS}:
                continue
            self._scan_value(child, active_aliases, f"{location} {path_name(name)}")

    @staticmethod
    def _reject_named_color_slot(
        value: pikepdf.Dictionary | pikepdf.Stream,
        name: pikepdf.Name,
        aliases: frozenset[str],
        location: str,
    ) -> None:
        if name not in value:
            return
        hits = _color_space_alias_hits(value[name], aliases)
        if hits:
            label = "color-space field" if name == pikepdf.Name.ColorSpace else "CS field"
            _raise_dependency(hits, f"{location} {path_name(name)}", label)

    def _reject_tiling_pattern_content(
        self,
        value: pikepdf.Dictionary | pikepdf.Stream,
        aliases: frozenset[str],
        location: str,
    ) -> None:
        if not isinstance(value, pikepdf.Stream):
            return
        if value.get(pikepdf.Name.PatternType, None) != 1:
            return
        instructions = pikepdf.parse_content_stream(value)
        hits = _content_alias_hits(instructions, aliases)
        if hits:
            _raise_dependency(hits, location, "tiling Pattern content stream")

    def _retain_direct(self, value: Any, key: ObjectKey) -> None:
        if key[0] == "direct":
            self.retained_direct_objects.append(value)


def reject_remaining_alias_dependencies(
    pdf: pikepdf.Pdf,
    spot: str,
    removals: tuple[ColorSpaceRemoval, ...],
) -> None:
    """Reject known references to aliases that the conversion plan would delete."""

    _AliasDependencyScanner(pdf, spot, removals).scan()


def _resource_target_aliases(
    resources: pikepdf.Dictionary,
    spot: str,
    planned_aliases: frozenset[str],
) -> frozenset[str]:
    color_spaces = resources.get(pikepdf.Name.ColorSpace, None)
    if not isinstance(color_spaces, pikepdf.Dictionary):
        return frozenset()
    return frozenset(
        pdf_name(name)
        for name, value in color_spaces.items()
        if pdf_name(name) in planned_aliases and separation_name(value) == spot
    )


def _needs_context_fallback(value: pikepdf.Dictionary | pikepdf.Stream) -> bool:
    return any(
        (
            pikepdf.Name.ColorSpace in value,
            pikepdf.Name.CS in value,
            pikepdf.Name.Resources in value,
            value.get(pikepdf.Name.Subtype, None) in {pikepdf.Name.Form, pikepdf.Name.Image},
            pikepdf.Name.PatternType in value,
            pikepdf.Name.ShadingType in value,
            value.get(pikepdf.Name.S, None) == pikepdf.Name.Transparency,
        )
    )


def _content_alias_hits(instructions: list[Any], aliases: frozenset[str]) -> frozenset[str]:
    hits: set[str] = set()
    for item in instructions:
        operator = operator_name(item)
        if operator == "INLINE IMAGE":
            color_space = item.iimage.obj.get(pikepdf.Name.ColorSpace, None)
            hits.update(_color_space_alias_hits(color_space, aliases))
            continue
        if operator not in {"cs", "CS"} or len(item.operands) != 1:
            continue
        operand = item.operands[0]
        if not isinstance(operand, pikepdf.Name):
            continue
        name = pdf_name(operand)
        if name in aliases and name not in _DIRECT_COLOR_SPACE_NAMES:
            hits.add(name)
    return frozenset(hits)


def _color_space_alias_hits(value: Any, aliases: frozenset[str]) -> frozenset[str]:
    hits: set[str] = set()
    _collect_color_space_alias_hits(value, aliases, hits, ObjectTracker())
    return frozenset(hits)


def _collect_color_space_alias_hits(
    value: Any,
    aliases: frozenset[str],
    hits: set[str],
    tracker: ObjectTracker,
) -> None:
    if isinstance(value, pikepdf.Name):
        name = pdf_name(value)
        if name in aliases and name not in _DIRECT_COLOR_SPACE_NAMES:
            hits.add(name)
        return
    if not isinstance(value, pikepdf.Array) or not value or not tracker.visit(value):
        return

    family = pdf_name(value[0])
    if family in {"Indexed", "Pattern"}:
        _collect_array_member(value, 1, aliases, hits, tracker)
        return
    if family in {"Separation", "DeviceN"}:
        _collect_array_member(value, 2, aliases, hits, tracker)
        if family == "DeviceN":
            _collect_devicen_attribute_hits(value, aliases, hits, tracker)
        return
    if family == "ICCBased" and len(value) > 1:
        profile = value[1]
        if isinstance(profile, pikepdf.Stream):
            alternate = profile.get(pikepdf.Name.Alternate, None)
            if alternate is not None:
                _collect_color_space_alias_hits(alternate, aliases, hits, tracker)


def _collect_array_member(
    value: pikepdf.Array,
    index: int,
    aliases: frozenset[str],
    hits: set[str],
    tracker: ObjectTracker,
) -> None:
    if len(value) > index:
        _collect_color_space_alias_hits(value[index], aliases, hits, tracker)


def _collect_devicen_attribute_hits(
    value: pikepdf.Array,
    aliases: frozenset[str],
    hits: set[str],
    tracker: ObjectTracker,
) -> None:
    if len(value) < 5 or not isinstance(value[4], pikepdf.Dictionary):
        return
    attributes = value[4]
    process = attributes.get(pikepdf.Name.Process, None)
    if isinstance(process, pikepdf.Dictionary):
        color_space = process.get(pikepdf.Name.ColorSpace, None)
        if color_space is not None:
            _collect_color_space_alias_hits(color_space, aliases, hits, tracker)
    colorants = attributes.get(pikepdf.Name.Colorants, None)
    if isinstance(colorants, pikepdf.Dictionary):
        for color_space in colorants.values():
            _collect_color_space_alias_hits(color_space, aliases, hits, tracker)


def _raise_dependency(hits: frozenset[str], location: str, kind: str) -> None:
    aliases = ", ".join(repr(name) for name in sorted(hits))
    raise UnsupportedSpotUseError(
        f"{location}: removable color-space alias(es) {aliases} are still referenced "
        f"by an unsupported {kind}"
    )


__all__ = ["reject_remaining_alias_dependencies"]
