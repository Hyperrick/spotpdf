"""Page SeparationInfo validation and rename slot planning."""

from typing import Any

import pikepdf

from .inventory_values import name_or_string
from .model import NameDependencyKind, UnsupportedSpotUseError
from .rename_hazards import name_field_mentions, separation_info_contains
from .rename_slots import SlotMode
from .rename_structures import validate_separation_page_group


def inspect_rename_page(builder, value: Any, page_label: str) -> None:
    if not isinstance(value, (pikepdf.Dictionary, pikepdf.Stream)):
        return
    info = value.get(pikepdf.Name.SeparationInfo, None)
    if info is None:
        return
    if not isinstance(info, pikepdf.Dictionary):
        raise UnsupportedSpotUseError(
            f"{page_label}: malformed /SeparationInfo", location=page_label
        )
    current = info.get(pikepdf.Name.DeviceColorant, None)
    current_name = name_or_string(current)
    color_space = info.get(pikepdf.Name.ColorSpace, None)
    color_space_contains = separation_info_contains(color_space, builder.source)
    target_names = frozenset({builder.source, builder.destination})
    if current_name is None and name_field_mentions(current, target_names):
        raise UnsupportedSpotUseError(
            f"{page_label}: target occurs in malformed /DeviceColorant", location=page_label
        )
    if color_space_contains is False and name_field_mentions(color_space, target_names):
        raise UnsupportedSpotUseError(
            f"{page_label}: target occurs in malformed /SeparationInfo /ColorSpace",
            location=page_label,
        )
    if current_name != builder.source and not color_space_contains:
        return
    if current_name != builder.source or color_space_contains is False:
        raise UnsupportedSpotUseError(
            f"{page_label}: /SeparationInfo /DeviceColorant and /ColorSpace disagree",
            location=page_label,
        )
    validate_separation_page_group(value, info, page_label)
    if isinstance(current, pikepdf.String):
        mode = SlotMode.STRING_VALUE
    elif isinstance(current, pikepdf.Name):
        mode = SlotMode.NAME_VALUE
    else:
        raise UnsupportedSpotUseError(
            f"{page_label}: malformed /SeparationInfo /DeviceColorant", location=page_label
        )
    locations = (f"{page_label} /SeparationInfo /DeviceColorant",)
    builder._add_slot(
        info,
        pikepdf.Name.DeviceColorant,
        mode,
        locations,
        dependency_kind=NameDependencyKind.SEPARATION_INFO,
    )
