"""Fail-closed prepress hazard detection for spot-color renames."""

from __future__ import annotations

from typing import Any

import pikepdf

from .colors import pdf_name
from .inventory_values import name_or_string, name_value, path_name
from .model import UnsupportedSpotUseError
from .objects import ObjectTracker
from .rename_slots import pdf_name as make_pdf_name


def inspect_hazards(
    value: Any,
    locations: tuple[str, ...],
    source: str,
    destination: str,
) -> None:
    """Reject target-related structures whose rename semantics are unsupported."""

    inspect_target_hazards(
        value,
        locations,
        frozenset({source, destination}),
        operation="atomic spot renames",
    )


def inspect_target_hazards(
    value: Any,
    locations: tuple[str, ...],
    names: frozenset[str],
    *,
    operation: str,
) -> None:
    """Reject the first unsupported prepress structure mentioning a target."""

    if not isinstance(value, (pikepdf.Dictionary, pikepdf.Stream)):
        return
    location = min(locations)
    hazards = target_name_hazards(value, names, operation=operation)
    if hazards:
        _, message = hazards[0]
        raise UnsupportedSpotUseError(f"{location}: {message}")


def target_name_hazards(
    value: Any,
    names: frozenset[str],
    *,
    operation: str,
) -> tuple[tuple[frozenset[str], str], ...]:
    """Return unsupported prepress name occurrences and their refusal messages."""

    if not names or not isinstance(value, (pikepdf.Dictionary, pikepdf.Stream)):
        return ()
    hazards: list[tuple[frozenset[str], str]] = []
    opi = value.get(pikepdf.Name.OPI, None)
    if opi is not None:
        hits = _subtree_hits(opi, names)
        if hits:
            hazards.append((hits, f"OPI colorant references are not supported for {operation}"))
    if value.get(pikepdf.Name.HalftoneType, None) == 5:
        hits = frozenset(name for name in names if make_pdf_name(name) in value)
        if hits:
            hazards.append((hits, "Type 5 halftone colorant names are not supported"))
    if value.get(pikepdf.Name.Subtype, None) == pikepdf.Name.Image:
        inks = value.get(pikepdf.Name.Inks, None)
        hits = frozenset(name for name in names if inks_value_has_any(inks, frozenset({name})))
        if hits:
            hazards.append((hits, "external image /Inks colorants are not supported"))

    if value.get(pikepdf.Name.Subtype, None) != pikepdf.Name.PrinterMark:
        return tuple(hazards)
    appearances = value.get(pikepdf.Name.AP, None)
    if not isinstance(appearances, pikepdf.Dictionary):
        return tuple(hazards)
    for key, label in ((pikepdf.Name.R, "AP/R"), (pikepdf.Name.D, "AP/D")):
        unsupported = appearances.get(key, None)
        if unsupported is None:
            continue
        hits = _subtree_hits(unsupported, names)
        if hits:
            hazards.append((hits, f"{label} appearances involving the target are not supported"))
    return tuple(hazards)


def _subtree_hits(value: Any, names: frozenset[str]) -> frozenset[str]:
    return frozenset(name for name in names if subtree_mentions(value, frozenset({name})))


def subtree_mentions(value: Any, names: frozenset[str]) -> bool:
    """Return whether a subtree contains a semantic colorant-name occurrence."""

    tracker = ObjectTracker()
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, pikepdf.Array):
            if not tracker.visit(current):
                continue
            family = name_value(current[0]) if current else None
            if family == "Separation" and len(current) >= 2 and name_value(current[1]) in names:
                return True
            if family == "DeviceN" and len(current) >= 2 and name_array_has_any(current[1], names):
                return True
            stack.extend(current)
            continue
        if not isinstance(current, (pikepdf.Dictionary, pikepdf.Stream)):
            continue
        if not tracker.visit(current):
            continue
        if _dictionary_mentions(current, names):
            return True
        stack.extend(current.values())
    return False


def devicen_target_mentions(value: pikepdf.Array, names: frozenset[str]) -> bool:
    """Detect target names in DeviceN name-bearing fields, including malformed ones."""

    components = value[1] if len(value) >= 2 else None
    if name_field_mentions(components, names):
        return True
    attributes = value[4] if len(value) >= 5 else None
    return name_or_string(attributes) in names or subtree_mentions(attributes, names)


def normal_appearance_forms(
    normal: Any,
    locations: tuple[str, ...],
) -> tuple[tuple[pikepdf.Stream, tuple[str, ...]], ...]:
    """Return Form streams from an annotation's normal appearance."""

    if isinstance(normal, pikepdf.Stream):
        if normal.get(pikepdf.Name.Subtype, None) != pikepdf.Name.Form:
            return ()
        return ((normal, tuple(f"{location} /AP /N" for location in locations)),)
    if not isinstance(normal, pikepdf.Dictionary):
        return ()
    return tuple(
        (
            appearance,
            tuple(f"{location} /AP /N {path_name(state)}" for location in locations),
        )
        for state, appearance in normal.items()
        if isinstance(appearance, pikepdf.Stream)
        and appearance.get(pikepdf.Name.Subtype, None) == pikepdf.Name.Form
    )


def is_matching_separation(value: Any, name: str) -> bool:
    """Return whether a value is one complete Separation for ``name``."""

    return (
        isinstance(value, pikepdf.Array)
        and len(value) == 4
        and name_value(value[0]) == "Separation"
        and name_value(value[1]) == name
    )


def name_array_contains(value: Any, name: str) -> bool:
    return isinstance(value, pikepdf.Array) and any(name_value(item) == name for item in value)


def name_array_has_any(value: Any, names: frozenset[str]) -> bool:
    return isinstance(value, pikepdf.Array) and any(name_value(item) in names for item in value)


def mixing_hints_contain(
    mixing: pikepdf.Dictionary,
    name: pikepdf.Name,
    decoded_name: str,
) -> bool:
    """Return whether supported MixingHints fields mention one colorant."""

    solidities = mixing.get(pikepdf.Name.Solidities, None)
    dot_gain = mixing.get(pikepdf.Name.DotGain, None)
    return any(
        (
            isinstance(solidities, pikepdf.Dictionary) and name in solidities,
            isinstance(dot_gain, pikepdf.Dictionary) and name in dot_gain,
            name_array_contains(mixing.get(pikepdf.Name.PrintingOrder, None), decoded_name),
        )
    )


def _dictionary_mentions(value: Any, names: frozenset[str]) -> bool:
    colorants = value.get(pikepdf.Name.Colorants, None)
    if name_field_mentions(colorants, names):
        return True
    for key in (pikepdf.Name.Solidities, pikepdf.Name.DotGain):
        field = value.get(key, None)
        if name_field_mentions(field, names):
            return True
    for key in (
        pikepdf.Name.PrintingOrder,
        pikepdf.Name.SeparationColorNames,
        pikepdf.Name.Components,
    ):
        if name_field_mentions(value.get(key, None), names):
            return True
    for key in (
        pikepdf.Name.Colorants,
        pikepdf.Name.Solidities,
        pikepdf.Name.DotGain,
        pikepdf.Name.Process,
        pikepdf.Name.MixingHints,
    ):
        if name_or_string(value.get(key, None)) in names:
            return True
    if inks_value_has_any(value.get(pikepdf.Name.Inks, None), names):
        return True
    return name_or_string(value.get(pikepdf.Name.DeviceColorant, None)) in names


def inks_value_has_any(value: Any, names: frozenset[str]) -> bool:
    """Find target names anywhere inside a valid or malformed /Inks value."""

    return name_field_mentions(value, names)


def name_field_mentions(value: Any, names: frozenset[str]) -> bool:
    """Scan one known name-bearing field, including malformed nested containers."""

    tracker = ObjectTracker()
    stack = [value]
    while stack:
        current = stack.pop()
        if name_or_string(current) in names:
            return True
        if isinstance(current, pikepdf.Array):
            if tracker.visit(current):
                stack.extend(current)
            continue
        if not isinstance(current, (pikepdf.Dictionary, pikepdf.Stream)):
            continue
        if not tracker.visit(current):
            continue
        if any(pdf_name(key) in names for key in current):
            return True
        stack.extend(current.values())
    return False


__all__ = [
    "devicen_target_mentions",
    "inspect_hazards",
    "inspect_target_hazards",
    "is_matching_separation",
    "mixing_hints_contain",
    "name_field_mentions",
    "name_array_contains",
    "normal_appearance_forms",
    "subtree_mentions",
    "target_name_hazards",
]
