"""Structural validation for existing Separation preview definitions."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

import pikepdf

from .inventory_graph import walk_reachable
from .inventory_values import name_or_string, name_value
from .model import UnsupportedSpotUseError
from .objects import object_key
from .rename_hazards import devicen_target_mentions, name_field_mentions

_DEVICE_COMPONENTS = {"DeviceGray": 1, "DeviceRGB": 3, "DeviceCMYK": 4}
_CIE_COMPONENTS = {"CalGray": 1, "CalRGB": 3, "Lab": 3}
_SAMPLED_BITS = {1, 2, 4, 8, 12, 16, 24, 32}


def validate_existing_preview(alternate: Any, tint: Any, location: str) -> None:
    """Require a supported alternate color space and dimensionally valid function."""

    outputs = _alternate_component_count(alternate, location, frozenset())
    if not _valid_function(
        tint,
        inputs=1,
        outputs=outputs,
        required_domain=(0, 1),
        stack=frozenset(),
    ):
        raise UnsupportedSpotUseError(
            f"{location}: malformed or unsupported Separation tint transform", location=location
        )


def reject_inline_target_definitions(pdf: pikepdf.Pdf, spot: str) -> None:
    """Reject target definitions embedded in inline-image content dictionaries."""

    names = frozenset({spot})
    for label, container in _content_containers(pdf):
        for instruction in pikepdf.parse_content_stream(container):
            if str(instruction.operator) != "INLINE IMAGE":
                continue
            color_space = instruction.iimage.obj.get(pikepdf.Name.ColorSpace, None)
            if _inline_space_mentions(color_space, names):
                raise UnsupportedSpotUseError(
                    f"{label}: inline-image color-space definition mentions {spot!r}",
                    location=label,
                )


def _alternate_component_count(
    value: Any,
    location: str,
    stack: frozenset[tuple[Any, ...]],
) -> int:
    family = name_value(value)
    if isinstance(value, pikepdf.Name) and family in _DEVICE_COMPONENTS:
        return _DEVICE_COMPONENTS[family]
    if not isinstance(value, pikepdf.Array) or len(value) != 2:
        raise UnsupportedSpotUseError(
            f"{location}: unsupported alternate color space", location=location
        )

    family = name_value(value[0])
    if family in _CIE_COMPONENTS and _valid_cie_space(family, value[1]):
        return _CIE_COMPONENTS[family]
    if family != "ICCBased" or not isinstance(value[1], pikepdf.Stream):
        raise UnsupportedSpotUseError(
            f"{location}: malformed alternate color space", location=location
        )

    profile = value[1]
    key = object_key(profile)
    if key in stack or len(stack) >= 16:
        raise UnsupportedSpotUseError(
            f"{location}: cyclic ICCBased alternate color space", location=location
        )
    count = profile.get(pikepdf.Name.N, None)
    if not _plain_int(count) or count not in {1, 3, 4}:
        raise UnsupportedSpotUseError(
            f"{location}: malformed ICCBased component count", location=location
        )
    if not _optional_intervals(profile.get(pikepdf.Name.Range, None), count, strict=False):
        raise UnsupportedSpotUseError(f"{location}: malformed ICCBased range", location=location)
    fallback = profile.get(pikepdf.Name.Alternate, None)
    if (
        fallback is not None
        and _alternate_component_count(
            fallback,
            location,
            stack | {key},
        )
        != count
    ):
        raise UnsupportedSpotUseError(
            f"{location}: ICCBased alternate dimensions disagree", location=location
        )
    return count


def _valid_cie_space(family: str, value: Any) -> bool:
    if not isinstance(value, pikepdf.Dictionary):
        return False
    white = value.get(pikepdf.Name.WhitePoint, None)
    if not _numeric_array(white, 3):
        return False
    if white[0] <= 0 or white[1] != 1 or white[2] <= 0:
        return False
    black = value.get(pikepdf.Name.BlackPoint, None)
    if black is not None and (
        not _numeric_array(black, 3) or any(component < 0 for component in black)
    ):
        return False
    if family == "CalGray":
        gamma = value.get(pikepdf.Name.Gamma, 1)
        return _pdf_number(gamma) and gamma > 0
    if family == "CalRGB":
        gamma = value.get(pikepdf.Name.Gamma, None)
        matrix = value.get(pikepdf.Name.Matrix, None)
        return (
            gamma is None or (_numeric_array(gamma, 3) and all(item > 0 for item in gamma))
        ) and (matrix is None or _numeric_array(matrix, 9))
    lab_range = value.get(pikepdf.Name.Range, None)
    return lab_range is None or _valid_intervals(lab_range, 2, strict=True)


def _valid_function(
    value: Any,
    *,
    inputs: int,
    outputs: int,
    required_domain: tuple[int, ...] | None,
    stack: frozenset[tuple[Any, ...]],
) -> bool:
    if not isinstance(value, (pikepdf.Dictionary, pikepdf.Stream)):
        return False
    key = object_key(value)
    if key in stack or len(stack) >= 16:
        return False
    stack |= {key}
    domain = value.get(pikepdf.Name.Domain, None)
    if not _valid_intervals(domain, inputs, strict=True):
        return False
    if required_domain is not None and tuple(domain) != required_domain:
        return False
    function_range = value.get(pikepdf.Name.Range, None)
    if not _optional_intervals(function_range, outputs, strict=False):
        return False
    function_type = value.get(pikepdf.Name.FunctionType, None)
    if not _plain_int(function_type):
        return False

    if function_type == 0:
        size = value.get(pikepdf.Name.Size, None)
        bits = value.get(pikepdf.Name.BitsPerSample, None)
        order = value.get(pikepdf.Name.Order, 1)
        return (
            isinstance(value, pikepdf.Stream)
            and function_range is not None
            and isinstance(size, pikepdf.Array)
            and len(size) == inputs
            and all(_plain_int(item) and item > 0 for item in size)
            and _plain_int(bits)
            and bits in _SAMPLED_BITS
            and _plain_int(order)
            and order in {1, 3}
            and _optional_numeric_array(value.get(pikepdf.Name.Encode, None), 2 * inputs)
            and _optional_numeric_array(value.get(pikepdf.Name.Decode, None), 2 * outputs)
        )
    if function_type == 2:
        return (
            not isinstance(value, pikepdf.Stream)
            and _optional_output_array(value.get(pikepdf.Name.C0, None), outputs)
            and _optional_output_array(value.get(pikepdf.Name.C1, None), outputs)
            and _pdf_number(value.get(pikepdf.Name.N, None))
        )
    if function_type == 3:
        functions = value.get(pikepdf.Name.Functions, None)
        bounds = value.get(pikepdf.Name.Bounds, None)
        encode = value.get(pikepdf.Name.Encode, None)
        if (
            isinstance(value, pikepdf.Stream)
            or inputs != 1
            or not isinstance(functions, pikepdf.Array)
            or not functions
            or not _numeric_array(bounds, len(functions) - 1)
            or not _numeric_array(encode, 2 * len(functions))
        ):
            return False
        if list(bounds) != sorted(bounds) or any(
            not domain[0] < bound < domain[1] for bound in bounds
        ):
            return False
        return all(
            _valid_function(
                function,
                inputs=1,
                outputs=outputs,
                required_domain=None,
                stack=stack,
            )
            for function in functions
        )
    if function_type == 4:
        return isinstance(value, pikepdf.Stream) and function_range is not None
    return False


def _optional_output_array(value: Any, outputs: int) -> bool:
    if value is None:
        return outputs == 1
    return _numeric_array(value, outputs)


def _optional_numeric_array(value: Any, length: int) -> bool:
    return value is None or _numeric_array(value, length)


def _optional_intervals(value: Any, pairs: int, *, strict: bool) -> bool:
    return value is None or _valid_intervals(value, pairs, strict=strict)


def _valid_intervals(value: Any, pairs: int, *, strict: bool) -> bool:
    if not _numeric_array(value, 2 * pairs):
        return False
    return all(
        low < high if strict else low <= high
        for low, high in zip(value[::2], value[1::2], strict=True)
    )


def _numeric_array(value: Any, length: int) -> bool:
    return (
        isinstance(value, pikepdf.Array)
        and len(value) == length
        and all(_pdf_number(item) for item in value)
    )


def _plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _pdf_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _inline_space_mentions(value: Any, names: frozenset[str]) -> bool:
    if not isinstance(value, pikepdf.Array) or not value:
        return False
    family = name_or_string(value[0])
    if family == "Separation":
        field = value[1] if len(value) >= 2 else None
        return name_field_mentions(field, names)
    return family == "DeviceN" and devicen_target_mentions(value, names)


def _content_containers(pdf: pikepdf.Pdf):
    for page_number, page in enumerate(pdf.pages, start=1):
        yield f"page {page_number}", page
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
        yield f"Form at {min(visit.locations)}", value


__all__ = ["reject_inline_target_definitions", "validate_existing_preview"]
