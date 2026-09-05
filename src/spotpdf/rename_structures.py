"""Target-related structural validation for supported prepress rename contexts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pikepdf

from .inventory_values import color_space_name, name_value
from .model import UnsupportedSpotUseError
from .objects import object_key

_DEVICE_COMPONENT_COUNTS = {
    "DeviceGray": 1,
    "DeviceRGB": 3,
    "DeviceCMYK": 4,
}
_CIE_COMPONENT_COUNTS = {
    "CalGray": 1,
    "CalRGB": 3,
    "Lab": 3,
}


@dataclass(frozen=True)
class ProcessStructure:
    """Validated Process mapping needed for NChannel role checks."""

    components: pikepdf.Array
    names: tuple[str, ...]
    is_cmyk: bool


def validate_process_dictionary(
    process: Any,
    device_names: tuple[str, ...],
    location: str,
) -> ProcessStructure | None:
    """Validate required DeviceN Process keys and return its component array."""

    if process is None:
        return None
    if not isinstance(process, pikepdf.Dictionary):
        raise UnsupportedSpotUseError(
            f"{location}: malformed DeviceN /Process dictionary", location=location
        )
    process_space = process.get(pikepdf.Name.ColorSpace, None)
    component_count, is_cmyk = _process_component_info(process_space, location)
    components = process.get(pikepdf.Name.Components, None)
    if not isinstance(components, pikepdf.Array) or any(
        name_value(item) is None for item in components
    ):
        raise UnsupportedSpotUseError(
            f"{location}: malformed /Process /Components", location=location
        )
    component_names = tuple(name_value(item) for item in components)
    if len(component_names) != component_count or len(set(component_names)) != component_count:
        raise UnsupportedSpotUseError(
            f"{location}: /Process /Components do not match /ColorSpace dimensions",
            location=location,
        )
    positions = [device_names.index(name) for name in component_names if name in device_names]
    if not positions:
        raise UnsupportedSpotUseError(
            f"{location}: /Process has no component in the DeviceN names array", location=location
        )
    if not is_cmyk and (
        positions != sorted(positions)
        or positions != list(range(min(positions), max(positions) + 1))
    ):
        raise UnsupportedSpotUseError(
            f"{location}: non-CMYK process components are not sequential and ordered",
            location=location,
        )
    return ProcessStructure(components, component_names, is_cmyk)


def validate_colorants_dictionary(
    colorants: Any,
    device_names: tuple[str, ...],
    subtype: str | None,
    process: ProcessStructure | None,
    location: str,
) -> None:
    """Validate Colorants entries and NChannel coverage of every spot component."""

    if colorants is not None and not isinstance(colorants, pikepdf.Dictionary):
        raise UnsupportedSpotUseError(
            f"{location}: malformed DeviceN /Colorants dictionary", location=location
        )
    if isinstance(colorants, pikepdf.Dictionary):
        for key, separation in colorants.items():
            key_name = str(key).removeprefix("/")
            if not (
                isinstance(separation, pikepdf.Array)
                and len(separation) == 4
                and name_value(separation[0]) == "Separation"
                and name_value(separation[1]) == key_name
            ):
                raise UnsupportedSpotUseError(
                    f"{location}: /Colorants key and Separation definition do not match",
                    location=location,
                )
    if subtype != "NChannel":
        return
    process_names = set(process.names) if process is not None else set()
    if process is not None and process.is_cmyk:
        process_names.update(("Cyan", "Magenta", "Yellow", "Black"))
    spot_names = set(device_names) - process_names - {"None"}
    missing = {
        name
        for name in spot_names
        if not isinstance(colorants, pikepdf.Dictionary)
        or pikepdf.Name(f"/{name}") not in colorants
    }
    if missing:
        names = ", ".join(sorted(missing, key=str.casefold))
        raise UnsupportedSpotUseError(
            f"{location}: NChannel /Colorants omits spot component(s): {names}", location=location
        )


def validate_mixing_hints(
    mixing: Any,
    component_names: tuple[str, ...],
    location: str,
) -> pikepdf.Dictionary | None:
    """Validate required MixingHints name relationships and basic value types."""

    if mixing is None:
        return None
    if not isinstance(mixing, pikepdf.Dictionary):
        raise UnsupportedSpotUseError(
            f"{location}: malformed /MixingHints dictionary", location=location
        )

    solidities = mixing.get(pikepdf.Name.Solidities, None)
    printing_order = mixing.get(pikepdf.Name.PrintingOrder, None)
    if solidities is not None:
        if not isinstance(solidities, pikepdf.Dictionary):
            raise UnsupportedSpotUseError(
                f"{location}: malformed /MixingHints /Solidities", location=location
            )
        for solidity in solidities.values():
            if not _is_pdf_number(solidity) or not 0 <= solidity <= 1:
                raise UnsupportedSpotUseError(
                    f"{location}: /MixingHints /Solidities values must be between 0 and 1",
                    location=location,
                )
        if printing_order is None:
            raise UnsupportedSpotUseError(
                f"{location}: /PrintingOrder is required when /Solidities is present",
                location=location,
            )

    if printing_order is not None:
        if not isinstance(printing_order, pikepdf.Array) or any(
            name_value(item) is None for item in printing_order
        ):
            raise UnsupportedSpotUseError(
                f"{location}: malformed /PrintingOrder array", location=location
            )
        order_names = {name_value(item) for item in printing_order}
        missing = set(component_names) - order_names
        if missing:
            names = ", ".join(sorted(missing, key=str.casefold))
            raise UnsupportedSpotUseError(
                f"{location}: /PrintingOrder omits DeviceN component(s): {names}", location=location
            )
    dot_gain = mixing.get(pikepdf.Name.DotGain, None)
    if dot_gain is not None and (
        not isinstance(dot_gain, pikepdf.Dictionary)
        or any(not _is_one_to_one_function(function) for function in dot_gain.values())
    ):
        raise UnsupportedSpotUseError(
            f"{location}: /MixingHints /DotGain values must be 1-to-1 functions", location=location
        )
    return mixing


def validate_separation_page_group(
    page: pikepdf.Dictionary,
    info: pikepdf.Dictionary,
    page_label: str,
) -> None:
    """Validate the required Pages array for a target SeparationInfo entry."""

    pages = info.get(pikepdf.Name.Pages, None)
    page_keys = _page_reference_keys(pages, page_label)
    if object_key(page) not in page_keys:
        raise UnsupportedSpotUseError(
            f"{page_label}: /SeparationInfo /Pages does not contain the current page",
            location=page_label,
        )
    for member in pages:
        nested_info = member.get(pikepdf.Name.SeparationInfo, None)
        if not isinstance(nested_info, pikepdf.Dictionary):
            raise UnsupportedSpotUseError(
                f"{page_label}: grouped page lacks a /SeparationInfo dictionary",
                location=page_label,
            )
        nested_pages = nested_info.get(pikepdf.Name.Pages, None)
        if _page_reference_keys(nested_pages, page_label) != page_keys:
            raise UnsupportedSpotUseError(
                f"{page_label}: grouped /SeparationInfo /Pages arrays disagree", location=page_label
            )


def _page_reference_keys(value: Any, page_label: str) -> tuple[tuple[Any, ...], ...]:
    if not isinstance(value, pikepdf.Array) or not value:
        raise UnsupportedSpotUseError(
            f"{page_label}: malformed /SeparationInfo /Pages array", location=page_label
        )
    keys: list[tuple[Any, ...]] = []
    for member in value:
        key = object_key(member)
        if (
            not isinstance(member, pikepdf.Dictionary)
            or member.get(pikepdf.Name.Type, None) != pikepdf.Name.Page
            or key[0] != "indirect"
        ):
            raise UnsupportedSpotUseError(
                f"{page_label}: /SeparationInfo /Pages must contain indirect page references",
                location=page_label,
            )
        keys.append(key)
    return tuple(keys)


def _process_component_info(value: Any, location: str) -> tuple[int, bool]:
    family = color_space_name(value)
    if isinstance(value, pikepdf.Name) and family in _DEVICE_COMPONENT_COUNTS:
        return _DEVICE_COMPONENT_COUNTS[family], family == "DeviceCMYK"
    if not isinstance(value, pikepdf.Array) or len(value) != 2:
        raise UnsupportedSpotUseError(
            f"{location}: malformed /Process /ColorSpace", location=location
        )
    if family in _CIE_COMPONENT_COUNTS and isinstance(value[1], pikepdf.Dictionary):
        return _CIE_COMPONENT_COUNTS[family], False
    if family == "ICCBased" and isinstance(value[1], pikepdf.Stream):
        count = value[1].get(pikepdf.Name.N, None)
        if isinstance(count, int) and not isinstance(count, bool) and count in {1, 3, 4}:
            return count, count == 4
    raise UnsupportedSpotUseError(f"{location}: malformed /Process /ColorSpace", location=location)


def _is_one_to_one_function(
    value: Any,
    *,
    stack: frozenset[tuple[Any, ...]] = frozenset(),
) -> bool:
    if not isinstance(value, (pikepdf.Dictionary, pikepdf.Stream)):
        return False
    key = object_key(value)
    if key in stack or len(stack) >= 16:
        return False
    stack |= {key}
    if not _is_numeric_array(value.get(pikepdf.Name.Domain, None), length=2, exact=(0, 1)):
        return False
    function_type = value.get(pikepdf.Name.FunctionType, None)
    if not isinstance(function_type, int) or isinstance(function_type, bool):
        return False
    function_range = value.get(pikepdf.Name.Range, None)
    if function_range is not None and not _is_numeric_array(function_range, length=2):
        return False
    if function_type == 0:
        size = value.get(pikepdf.Name.Size, None)
        bits = value.get(pikepdf.Name.BitsPerSample, None)
        order = value.get(pikepdf.Name.Order, 1)
        encode = value.get(pikepdf.Name.Encode, None)
        decode = value.get(pikepdf.Name.Decode, None)
        return (
            isinstance(value, pikepdf.Stream)
            and isinstance(size, pikepdf.Array)
            and len(size) == 1
            and isinstance(size[0], int)
            and not isinstance(size[0], bool)
            and size[0] > 0
            and isinstance(bits, int)
            and not isinstance(bits, bool)
            and bits in {1, 2, 4, 8, 12, 16, 24, 32}
            and isinstance(order, int)
            and not isinstance(order, bool)
            and order in {1, 3}
            and _is_optional_numeric_array(encode, length=2)
            and _is_optional_numeric_array(decode, length=2)
            and function_range is not None
        )
    if function_type == 2:
        return (
            not isinstance(value, pikepdf.Stream)
            and _is_optional_numeric_array(value.get(pikepdf.Name.C0, None), length=1)
            and _is_optional_numeric_array(value.get(pikepdf.Name.C1, None), length=1)
            and _is_pdf_number(value.get(pikepdf.Name.N, None))
        )
    if function_type == 3:
        functions = value.get(pikepdf.Name.Functions, None)
        if not isinstance(functions, pikepdf.Array) or not functions:
            return False
        bounds = value.get(pikepdf.Name.Bounds, None)
        encode = value.get(pikepdf.Name.Encode, None)
        if not _is_numeric_array(bounds, length=len(functions) - 1) or not _is_numeric_array(
            encode,
            length=2 * len(functions),
        ):
            return False
        if any(not 0 < bound < 1 for bound in bounds) or list(bounds) != sorted(bounds):
            return False
        return all(_is_one_to_one_function(function, stack=stack) for function in functions)
    if function_type == 4:
        return isinstance(value, pikepdf.Stream) and function_range is not None
    return False


def _is_optional_numeric_array(value: Any, *, length: int) -> bool:
    return value is None or _is_numeric_array(value, length=length)


def _is_numeric_array(
    value: Any,
    *,
    length: int,
    exact: tuple[int, ...] | None = None,
) -> bool:
    if not isinstance(value, pikepdf.Array) or len(value) != length:
        return False
    if not all(_is_pdf_number(item) for item in value):
        return False
    return exact is None or tuple(value) == exact


def _is_pdf_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float, Decimal))


__all__ = [
    "ProcessStructure",
    "validate_colorants_dictionary",
    "validate_mixing_hints",
    "validate_process_dictionary",
    "validate_separation_page_group",
]
